import logging
from typing import Dict, Any, List, Optional

from ..models.property import (
    Decision,
    PropertyType,
    PropertyAnalysisResult,
    PropertyObservations,
    ParcelShape,
    WaterBodyType,
    MedicalType,
    SchoolType,
    ReligiousBuildingType,
    CommercialRejectType,
    NeighborhoodDensity,
)

logger = logging.getLogger(__name__)

_GIS_RESIDENTIAL_KEYWORDS = {"single family", "residential", "sfr", "duplex", "multi-family", "condo", "townhouse"}
_GIS_COMMERCIAL_KEYWORDS  = {"commercial", "retail", "office", "industrial", "warehouse"}
_GIS_VACANT_KEYWORDS      = {"vacant", "unimproved", "open land", "undeveloped"}
_GIS_AGRICULTURE_KEYWORDS = {"agriculture", "farm", "ranch", "timberland", "grove"}

_TINY_PARCEL_SQFT   = 800
_MIN_BUILDABLE_SQFT = 2_000

# Confidence below this → force NEEDS_HUMAN_REVIEW (per SOP reference in models)
_LOW_CONFIDENCE_THRESHOLD = 0.65


class RuleEngine:
    """
    Deterministic SOP rule engine — Alterna Tax Certificate Fund.
    Implements all SOP rules and exceptions exactly as defined in property.py.
    """

    def evaluate(
        self,
        vision_result: Optional[Dict[str, Any]],
        parcel_meta: Optional[Dict[str, Any]] = None,
    ) -> PropertyAnalysisResult:
        vr = vision_result or {}
        parcel = parcel_meta or {}

        # Vision service signals no images were available — cannot make automated decision
        no_images = bool(vr.get("_no_images"))

        obs = self._parse_observations(vr)

        # Aerial availability — if no satellite image was captured, force REVIEW
        aerial_available = bool(parcel.get("aerial_image_available", True)) and not no_images

        prop_type_raw = vr.get("property_type", "unknown")
        if prop_type_raw in ("unknown", ""):
            gis_type = self._gis_property_type(parcel)
            if gis_type:
                prop_type_raw = gis_type
                logger.info(f"Property type corrected by GIS: {gis_type}")

        prop_type = self._parse_property_type(prop_type_raw)

        raw_confidence = float(vr.get("confidence", vr.get("confidence_score", 0.0)))
        if raw_confidence == 0.0:
            raw_confidence = self._infer_confidence(vr)
        confidence = self._adjust_confidence_with_gis(raw_confidence, parcel)
        confidence = self._boost_confidence_for_completeness(confidence, vr, prop_type_raw)

        rejection_reasons: List[str] = []
        review_reasons: List[str] = []

        # --- No aerial → force review regardless of everything else ---
        if not aerial_available:
            review_reasons.append("No aerial/satellite image available — manual review required")
        else:
            # Global banned-facility check (catches misclassified property types)
            banned_reasons = self._check_banned_facility_global(obs)
            rejection_reasons.extend(banned_reasons)

            if not banned_reasons:
                if prop_type == PropertyType.RESIDENTIAL:
                    rejection_reasons.extend(self._check_residential(obs))
                    review_reasons.extend(self._residential_review_flags(obs))
                elif prop_type == PropertyType.MOBILE_HOME:
                    rejection_reasons.extend(self._check_residential(obs))
                    review_reasons.append("Mobile home / manufactured housing — requires underwriter review")
                elif prop_type == PropertyType.INDUSTRIAL:
                    review_reasons.append("Industrial property — requires underwriter review")
                elif prop_type == PropertyType.COMMERCIAL:
                    rejection_reasons.extend(self._check_commercial(obs))
                elif prop_type == PropertyType.VACANT_LAND:
                    rejection_reasons.extend(self._check_vacant_land(obs))
                elif prop_type == PropertyType.AGRICULTURE:
                    rejection_reasons.extend(self._check_agriculture(obs))
                    if not rejection_reasons:
                        review_reasons.extend(self._agriculture_review_flags(obs))
                else:
                    review_reasons.append("Property type undetermined — manual review required")

            # GIS parcel size + consistency checks
            rejection_reasons.extend(self._check_parcel_size(prop_type, parcel))
            review_reasons.extend(self._check_gis_consistency(prop_type, vr, parcel))

        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            review_reasons.append(
                f"Low confidence ({confidence:.0%}) — image quality insufficient for automated decision"
            )

        if rejection_reasons:
            decision = Decision.REJECTED
        elif review_reasons:
            decision = Decision.NEEDS_HUMAN_REVIEW
        else:
            decision = Decision.APPROVED

        ai_summary = vr.get("notes", vr.get("summary", ""))
        summary = self._build_summary(decision, prop_type, obs, rejection_reasons, review_reasons, ai_summary)

        return PropertyAnalysisResult(
            decision=decision,
            property_type=prop_type,
            confidence_score=confidence,
            observations=obs,
            rejection_reasons=rejection_reasons,
            human_review_reasons=review_reasons,
            summary=summary,
            aerial_image_available=aerial_available,
            primary_image_used="aerial" if aerial_available else "street_view",
        )

    # ── Global checks ─────────────────────────────────────────────────────────

    def _check_banned_facility_global(self, obs: PropertyObservations) -> List[str]:
        """Catches banned facilities regardless of what property_type the model returned."""
        reasons = []
        if obs.medical_type == MedicalType.HOSPITAL:
            reasons.append("Hospital detected — ineligible per SOP")
        if obs.school_type == SchoolType.SCHOOL:
            reasons.append("K-12 school detected — ineligible per SOP")
        if obs.religious_building_type != ReligiousBuildingType.NONE:
            reasons.append(f"Religious building detected ({obs.religious_building_type.value}) — ineligible per SOP")
        if obs.commercial_reject_type != CommercialRejectType.NONE:
            reasons.append(f"Ineligible commercial facility ({obs.commercial_reject_type.value}) — ineligible per SOP")
        return reasons

    # ── SOP checks ────────────────────────────────────────────────────────────

    def _check_residential(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.boarded_windows and not obs.hurricane_shutters:
            reasons.append("Plywood-boarded windows detected — property condemned or abandoned")
        if obs.structure_burned:
            reasons.append("Fire/burn damage to structure")
        if obs.visible_structure_damage:
            reasons.append("Visible structural damage")
        if obs.roof_damage:
            reasons.append("Significant roof damage visible")
        if obs.under_construction:
            reasons.append("Property under active construction — structure incomplete")
        if obs.trash_or_debris:
            reasons.append("Heavy trash/debris covering property — uninhabitable per SOP")
        return reasons

    def _residential_review_flags(self, obs: PropertyObservations) -> List[str]:
        flags = []
        if obs.mobile_home:
            flags.append("Mobile/manufactured home detected — requires underwriter review")
        if not obs.has_structure:
            flags.append("No permanent structure detected on parcel — verify property type")
        return flags

    def _check_commercial(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.parcel_is_parking_only:
            reasons.append("Parcel contains only a parking lot — no insurable structure")
        return reasons

    def _check_vacant_land(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.parcel_shape == ParcelShape.NARROW or obs.side_lot:
            reasons.append("Narrow/side lot — unbuildable, no usable street frontage")
        if obs.parcel_shape == ParcelShape.TRIANGLE:
            reasons.append("Triangle-shaped lot — not buildable")
        if not obs.street_frontage:
            reasons.append("No street frontage — rear/alley lot, not independently buildable")
        if obs.landlocked:
            reasons.append("Landlocked parcel — no road access")
        if not obs.road_access:
            reasons.append("No road access visible")
        if obs.wooded:
            reasons.append("Heavily wooded — buildability concern")
        if not obs.lot_size_adequate_vs_neighborhood:
            reasons.append("Lot appears significantly undersized vs neighboring parcels — unbuildable")
        return reasons

    def _check_agriculture(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        # WATER_HOLE inside parcel = drainage problem → REJECT
        if obs.water_body_type == WaterBodyType.WATER_HOLE:
            reasons.append("Water hole/saturated depression inside parcel — likely drainage issue (SOP reject)")
        if obs.landlocked:
            reasons.append("Landlocked parcel — no road access")
        if obs.parcel_shape in (ParcelShape.TRIANGLE, ParcelShape.NARROW):
            reasons.append(f"Parcel shape ({obs.parcel_shape.value}) unsuitable for agriculture")
        return reasons

    def _agriculture_review_flags(self, obs: PropertyObservations) -> List[str]:
        """Agriculture: SOP approves ONLY when all 3 positive criteria are met."""
        flags = []
        if not obs.agri_has_house_on_parcel:
            flags.append("No house/structure on agricultural parcel — SOP requires dwelling on parcel")
        if not obs.agri_fronts_road:
            flags.append("Agricultural parcel does not appear to front a road — verify access")
        if not obs.agri_parcel_shape_regular:
            flags.append("Parcel shape is not rectangular/square — SOP prefers regular-shaped agriculture parcels")
        return flags

    # ── GIS-based checks ──────────────────────────────────────────────────────

    def _check_parcel_size(self, prop_type: PropertyType, parcel: Dict[str, Any]) -> List[str]:
        reasons = []
        area = parcel.get("area_sqft")
        if area is None or parcel.get("properties", {}).get("estimated"):
            return reasons
        if area < _TINY_PARCEL_SQFT:
            reasons.append(f"Extremely small parcel ({area:,.0f} sqft) — verify boundaries")
        elif area < _MIN_BUILDABLE_SQFT and prop_type == PropertyType.VACANT_LAND:
            reasons.append(f"Parcel too small ({area:,.0f} sqft) to be buildable")
        return reasons

    def _check_gis_consistency(
        self, prop_type: PropertyType, vr: Dict[str, Any], parcel: Dict[str, Any]
    ) -> List[str]:
        flags = []
        props = parcel.get("properties", {})
        if not props or props.get("estimated"):
            return flags
        usedesc = str(props.get("usedesc", "")).lower()
        improvval = props.get("improvval")
        if any(k in usedesc for k in _GIS_VACANT_KEYWORDS) and vr.get("has_structure"):
            flags.append("GIS records show vacant land but vision detected a structure — verify")
        if improvval is not None and float(improvval) == 0 and vr.get("has_structure"):
            flags.append("GIS shows $0 improvement value but structure detected — verify records")
        if any(k in usedesc for k in _GIS_RESIDENTIAL_KEYWORDS) and prop_type == PropertyType.COMMERCIAL:
            flags.append("Vision classified as commercial but GIS shows residential zoning — verify")
        return flags

    def _gis_property_type(self, parcel: Dict[str, Any]) -> Optional[str]:
        props = parcel.get("properties", {})
        if not props or props.get("estimated"):
            return None
        usedesc = str(props.get("usedesc", "") or props.get("proptype", "")).lower()
        if any(k in usedesc for k in _GIS_RESIDENTIAL_KEYWORDS):
            return "residential"
        if any(k in usedesc for k in _GIS_COMMERCIAL_KEYWORDS):
            return "commercial"
        if any(k in usedesc for k in _GIS_VACANT_KEYWORDS):
            return "vacant_land"
        if any(k in usedesc for k in _GIS_AGRICULTURE_KEYWORDS):
            return "agriculture"
        return None

    def _adjust_confidence_with_gis(self, base: float, parcel: Dict[str, Any]) -> float:
        props = parcel.get("properties", {})
        if not props or props.get("estimated"):
            return base
        return round(min(base + 0.05, 0.95), 2)

    def _boost_confidence_for_completeness(
        self, base: float, vr: Dict[str, Any], prop_type_raw: str
    ) -> float:
        """Add up to +0.06 when the vision result is thorough and internally consistent.
        This corrects the model's tendency to under-report confidence on clear images."""
        boost = 0.0

        # Property type is clearly identified (not unknown)
        if prop_type_raw not in ("unknown", ""):
            boost += 0.02

        # All critical boolean flags are explicitly present (model didn't skip them)
        critical = [
            "damaged_or_burned", "plywood_on_windows", "heavy_garbage_debris",
            "has_road_access", "has_structure", "heavily_wooded",
        ]
        answered = sum(1 for f in critical if vr.get(f) is True or vr.get(f) is False)
        if answered >= 5:
            boost += 0.02
        if answered == len(critical):
            boost += 0.01

        # Model wrote a meaningful notes field (shows it actually analysed the image)
        notes = str(vr.get("notes", "")).strip()
        if len(notes) > 40:
            boost += 0.01

        return round(min(base + boost, 0.95), 2)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _infer_confidence(self, vision_result: Dict[str, Any]) -> float:
        score = 0.0
        prop_type = vision_result.get("property_type", "unknown")
        if prop_type not in ("unknown", ""):
            score += 0.30
        else:
            score -= 0.10
        notes = vision_result.get("notes", vision_result.get("summary", "")).strip()
        if len(notes) > 60:
            score += 0.15
        elif len(notes) > 20:
            score += 0.08
        key_fields = [
            "damaged_or_burned", "plywood_on_windows", "heavy_garbage_debris",
            "hospital", "k12_school", "has_road_access", "street_frontage",
            "heavily_wooded", "water_hole_on_land", "has_structure",
            "vacancy_signs", "mobile_home", "under_construction",
        ]
        explicit = sum(1 for f in key_fields if vision_result.get(f) is True or vision_result.get(f) is False)
        score += min(0.35, explicit * 0.03)
        rbt = vision_result.get("religious_building_type", "none")
        crt = vision_result.get("commercial_reject_type", "none")
        if (rbt and rbt != "none") or (crt and crt != "none"):
            score += 0.05
        return round(max(0.1, min(score, 0.95)), 2)

    def _parse_observations(self, vr: Dict[str, Any]) -> PropertyObservations:
        try:
            damaged = bool(vr.get("damaged_or_burned", False))
            plywood = bool(vr.get("plywood_on_windows", False))
            debris  = bool(vr.get("heavy_garbage_debris", False))
            road    = bool(vr.get("has_road_access", True))

            # Parcel shape
            if vr.get("side_lot"):
                shape = ParcelShape.NARROW
            elif vr.get("triangle_lot"):
                shape = ParcelShape.TRIANGLE
            else:
                shape = ParcelShape.UNKNOWN

            # Water: water_hole = drainage problem inside parcel
            water = WaterBodyType.WATER_HOLE if vr.get("water_hole_on_land") else WaterBodyType.NONE

            # Religious building
            rbt_raw = str(vr.get("religious_building_type", "none")).lower().strip()
            rbt_map = {
                "church":    ReligiousBuildingType.CHURCH,
                "synagogue": ReligiousBuildingType.SYNAGOGUE,
                "mosque":    ReligiousBuildingType.MOSQUE,
                "temple":    ReligiousBuildingType.TEMPLE,
            }
            religious_type = rbt_map.get(rbt_raw, ReligiousBuildingType.NONE)

            # Commercial reject type
            crt_raw = str(vr.get("commercial_reject_type", "none")).lower().strip()
            crt_map = {
                "gas_station": CommercialRejectType.GAS_STATION,
                "auto_repair": CommercialRejectType.AUTO_REPAIR,
            }
            commercial_reject = crt_map.get(crt_raw, CommercialRejectType.NONE)

            # Medical / school (kept for hospital/school detection)
            med_type = MedicalType.HOSPITAL if bool(vr.get("hospital", False)) else MedicalType.NONE
            sch_type = SchoolType.SCHOOL if bool(vr.get("k12_school", False)) else SchoolType.NONE

            # Agriculture criteria
            is_agri = str(vr.get("property_type", "")).lower() == "agriculture"

            return PropertyObservations(
                boarded_windows=plywood,
                hurricane_shutters=False,
                roof_damage=False,
                visible_structure_damage=damaged,
                structure_burned=damaged,
                abandoned_appearance=debris,
                trash_or_debris=debris,
                vacancy_signs=bool(vr.get("vacancy_signs", False)),
                mobile_home=bool(vr.get("mobile_home", False)),
                under_construction=bool(vr.get("under_construction", False)),
                road_access=road,
                landlocked=not road,
                street_frontage=bool(vr.get("street_frontage", True)),
                side_lot=bool(vr.get("side_lot", False)),
                wooded=bool(vr.get("heavily_wooded", False)),
                lot_size_adequate_vs_neighborhood=bool(vr.get("lot_size_adequate", True)),
                water_body_type=water,
                parcel_shape=shape,
                buildable=True,
                has_structure=bool(vr.get("has_structure", False)),
                parcel_is_parking_only=bool(vr.get("parcel_is_parking_only", False)),
                medical_type=med_type,
                school_type=sch_type,
                religious_building_type=religious_type,
                commercial_reject_type=commercial_reject,
                agri_has_house_on_parcel=bool(vr.get("agri_has_house", False)) if is_agri else False,
                agri_fronts_road=bool(vr.get("agri_fronts_road", False)) if is_agri else False,
                agri_parcel_shape_regular=bool(vr.get("agri_shape_regular", False)) if is_agri else False,
                neighborhood_density=NeighborhoodDensity.UNKNOWN,
            )
        except Exception as e:
            logger.error(f"Observation parse error: {e}")
            return PropertyObservations()

    def _parse_property_type(self, raw: str) -> PropertyType:
        mapping = {
            "residential":  PropertyType.RESIDENTIAL,
            "commercial":   PropertyType.COMMERCIAL,
            "vacant_land":  PropertyType.VACANT_LAND,
            "agriculture":  PropertyType.AGRICULTURE,
            "mobile_home":  PropertyType.MOBILE_HOME,
            "industrial":   PropertyType.INDUSTRIAL,
        }
        return mapping.get((raw or "").lower(), PropertyType.UNKNOWN)

    def _build_summary(self, decision, prop_type, obs, rejections, reviews, ai_summary) -> str:
        parts = [f"Type: {prop_type.value}. Decision: {decision.value}."]
        if rejections:
            parts.append("Rejected: " + "; ".join(rejections) + ".")
        if reviews:
            parts.append("Review flags: " + "; ".join(reviews) + ".")
        if ai_summary:
            parts.append(ai_summary)
        return " ".join(parts)
