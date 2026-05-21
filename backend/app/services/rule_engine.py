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
    NeighborhoodDensity,
)

logger = logging.getLogger(__name__)

REJECTED_COMMERCIAL_KEYWORDS = {
    "church", "mosque", "synagogue", "temple", "chapel",
    "gas station", "fuel station", "petrol station",
    "auto repair", "auto shop", "mechanic", "car wash", "body shop",
    "cemetery", "funeral home", "mortuary",
    "power plant", "utility substation",
}

_GIS_RESIDENTIAL_KEYWORDS = {"single family", "residential", "sfr", "duplex", "multi-family", "condo", "townhouse"}
_GIS_COMMERCIAL_KEYWORDS  = {"commercial", "retail", "office", "industrial", "warehouse"}
_GIS_VACANT_KEYWORDS      = {"vacant", "unimproved", "open land", "undeveloped"}
_GIS_AGRICULTURE_KEYWORDS = {"agriculture", "farm", "ranch", "timberland", "grove"}

_TINY_PARCEL_SQFT   = 800
_MIN_BUILDABLE_SQFT = 2_000


class RuleEngine:
    """
    Deterministic SOP rule engine — Alterna Tax Certificate Fund.
    """

    def evaluate(
        self,
        vision_result: Optional[Dict[str, Any]],
        parcel_meta: Optional[Dict[str, Any]] = None,
    ) -> PropertyAnalysisResult:
        vr = vision_result or {}
        parcel = parcel_meta or {}

        obs = self._parse_observations(vr)

        prop_type_raw = vr.get("property_type", "unknown")
        if prop_type_raw in ("unknown", ""):
            gis_type = self._gis_property_type(parcel)
            if gis_type:
                prop_type_raw = gis_type
                logger.info(f"Property type corrected by GIS data: {gis_type}")

        prop_type = self._parse_property_type(prop_type_raw)

        raw_confidence = float(vr.get("confidence", vr.get("confidence_score", 0.0)))
        if raw_confidence == 0.0:
            raw_confidence = self._infer_confidence(vr)
        confidence = self._adjust_confidence_with_gis(raw_confidence, parcel)

        rejection_reasons: List[str] = []
        review_reasons: List[str] = []

        # Global banned-facility check — catches churches/hospitals misclassified as residential
        banned_reasons = self._check_banned_facility_global(obs)
        rejection_reasons.extend(banned_reasons)

        if not banned_reasons:
            if prop_type == PropertyType.RESIDENTIAL:
                rejection_reasons.extend(self._check_residential(obs))
                review_reasons.extend(self._residential_review_flags(obs))
            elif prop_type == PropertyType.COMMERCIAL:
                pass  # covered by global banned check
            elif prop_type == PropertyType.VACANT_LAND:
                rejection_reasons.extend(self._check_vacant_land(obs))
            elif prop_type == PropertyType.AGRICULTURE:
                rejection_reasons.extend(self._check_agriculture(obs))
                review_reasons.extend(self._agriculture_review_flags(obs))
            else:
                review_reasons.append("Property type undetermined — manual review required")

        rejection_reasons.extend(self._check_parcel_size(prop_type, parcel))
        review_reasons.extend(self._check_gis_consistency(prop_type, vr, parcel))

        if confidence < 0.4:
            review_reasons.append(
                f"Very low confidence ({confidence:.0%}) — image quality insufficient for automated decision"
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
        )

    # ── Global checks ─────────────────────────────────────────────────────────

    def _check_banned_facility_global(self, obs: PropertyObservations) -> List[str]:
        """Catches banned facilities regardless of what property_type the model returned."""
        reasons = []
        if obs.medical_type == MedicalType.HOSPITAL:
            reasons.append("Hospital detected — ineligible per SOP")
        if obs.school_type == SchoolType.SCHOOL:
            reasons.append("K-12 school detected — ineligible per SOP")
        ct = obs.commercial_type_detected.lower()
        for banned in REJECTED_COMMERCIAL_KEYWORDS:
            if banned in ct:
                reasons.append(f"Ineligible facility type: {ct}")
                break
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
        if obs.under_construction:
            reasons.append("Property under active construction — structure incomplete")
        if obs.abandoned_appearance and obs.trash_or_debris:
            reasons.append("Abandoned property with heavy debris — uninhabitable")
        return reasons

    def _residential_review_flags(self, obs: PropertyObservations) -> List[str]:
        flags = []
        if obs.mobile_home:
            flags.append("Mobile/manufactured home detected — requires underwriter review")
        if obs.vacancy_signs and not obs.trash_or_debris:
            flags.append("Property shows signs of vacancy — verify occupancy status")
        return flags

    def _check_vacant_land(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.parcel_shape == ParcelShape.NARROW:
            reasons.append("Narrow/side lot — unbuildable")
        if obs.parcel_shape == ParcelShape.TRIANGLE:
            reasons.append("Triangle-shaped lot — not buildable")
        if obs.landlocked:
            reasons.append("Landlocked parcel — no road access")
        if not obs.road_access:
            reasons.append("No road access visible")
        if obs.wooded:
            reasons.append("Heavily wooded — buildability concern")
        return reasons

    def _check_agriculture(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.water_body_type == WaterBodyType.POND:
            reasons.append("Isolated pond/water hole on land — likely drainage issue (SOP reject)")
        if not obs.road_access:
            reasons.append("No road access — agricultural parcel inaccessible")
        return reasons

    def _agriculture_review_flags(self, obs: PropertyObservations) -> List[str]:
        flags = []
        if not obs.has_structure:
            flags.append("No house/structure on agricultural parcel — SOP prefers parcel with a house")
        if obs.parcel_shape not in (ParcelShape.RECTANGULAR, ParcelShape.SQUARE, ParcelShape.UNKNOWN):
            flags.append(f"Parcel shape is {obs.parcel_shape.value} — SOP prefers rectangular/square")
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
            "banned_facility", "has_road_access", "heavily_wooded", "pond_on_land",
            "has_structure", "vacancy_signs", "mobile_home", "under_construction",
        ]
        explicit = sum(1 for f in key_fields if vision_result.get(f) is True or vision_result.get(f) is False)
        score += min(0.35, explicit * 0.04)
        fac = vision_result.get("banned_facility_type", "none")
        if fac and fac not in ("none", "n/a", ""):
            score += 0.05
        return round(max(0.1, min(score, 0.95)), 2)

    def _parse_observations(self, vr: Dict[str, Any]) -> PropertyObservations:
        try:
            damaged = bool(vr.get("damaged_or_burned", False))
            plywood = bool(vr.get("plywood_on_windows", False))
            debris  = bool(vr.get("heavy_garbage_debris", False))
            road    = bool(vr.get("has_road_access", True))

            if vr.get("narrow_strip_lot"):
                shape = ParcelShape.NARROW
            elif vr.get("triangle_lot"):
                shape = ParcelShape.TRIANGLE
            else:
                shape = ParcelShape.UNKNOWN

            water = WaterBodyType.POND if vr.get("pond_on_land") else WaterBodyType.NONE

            fac_raw = str(vr.get("banned_facility_type", "none")).lower().strip()
            if "hospital" in fac_raw:
                med_type, sch_type, comm_type = MedicalType.HOSPITAL, SchoolType.NONE, "none"
            elif any(k in fac_raw for k in ("school", "k-12", "k12", "elementary", "middle", "high school")):
                med_type, sch_type, comm_type = MedicalType.NONE, SchoolType.SCHOOL, "none"
            elif fac_raw in ("none", "", "n/a"):
                med_type, sch_type, comm_type = MedicalType.NONE, SchoolType.NONE, "none"
            else:
                med_type, sch_type, comm_type = MedicalType.NONE, SchoolType.NONE, fac_raw

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
                wooded=bool(vr.get("heavily_wooded", False)),
                water_body_type=water,
                parcel_shape=shape,
                buildable=True,
                has_structure=bool(vr.get("has_structure", False)),
                commercial_type_detected=comm_type,
                medical_type=med_type,
                school_type=sch_type,
                neighborhood_density=NeighborhoodDensity.UNKNOWN,
            )
        except Exception as e:
            logger.error(f"Observation parse error: {e}")
            return PropertyObservations()

    def _parse_property_type(self, raw: str) -> PropertyType:
        mapping = {
            "residential": PropertyType.RESIDENTIAL,
            "commercial":  PropertyType.COMMERCIAL,
            "vacant_land": PropertyType.VACANT_LAND,
            "agriculture": PropertyType.AGRICULTURE,
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
