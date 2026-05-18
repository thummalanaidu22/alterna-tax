import logging
from typing import Dict, Any, List, Optional, Tuple

from ..models.property import (
    Decision,
    PropertyType,
    PropertyAnalysisResult,
    PropertyObservations,
    ParcelShape,
)

logger = logging.getLogger(__name__)

REJECTED_COMMERCIAL_TYPES = {
    "hospital", "church", "mosque", "synagogue",
    "gas station", "auto repair", "auto shop",
    "mechanic", "car wash",
}

ALLOWED_COMMERCIAL_TYPES = {
    "retail", "apartment", "office", "condominium",
    "condo", "strip mall", "shopping center", "none",
}


class RuleEngine:
    """
    Deterministic SOP-based rule engine.
    Applies rejection/review rules to vision model observations.
    Can override or confirm AI decisions.
    """

    def evaluate(
        self,
        vision_result: Optional[Dict[str, Any]],
        parcel_meta: Optional[Dict[str, Any]] = None,
    ) -> PropertyAnalysisResult:
        obs_raw = (vision_result or {}).get("observations", {})
        obs = self._parse_observations(obs_raw)

        prop_type_raw = (vision_result or {}).get("property_type", "unknown")
        prop_type = self._parse_property_type(prop_type_raw)

        raw_confidence = float((vision_result or {}).get("confidence_score", 0.0))
        # 0.0 means the model left the placeholder — infer from result completeness
        if raw_confidence == 0.0:
            raw_confidence = self._infer_confidence(vision_result or {})
        confidence = raw_confidence

        ai_decision = (vision_result or {}).get("decision", "NEEDS_HUMAN_REVIEW")
        ai_summary = (vision_result or {}).get("summary", "")

        rejection_reasons: List[str] = []
        review_reasons: List[str] = []

        # Run type-specific rules
        if prop_type == PropertyType.RESIDENTIAL:
            rejection_reasons.extend(self._check_residential(obs))
        elif prop_type == PropertyType.COMMERCIAL:
            rejection_reasons.extend(self._check_commercial(obs))
        elif prop_type == PropertyType.VACANT_LAND:
            rejection_reasons.extend(self._check_vacant_land(obs))
        elif prop_type == PropertyType.AGRICULTURE:
            rejection_reasons.extend(self._check_agriculture(obs))
        else:
            review_reasons.append("Property type could not be determined")

        # Low confidence triggers review
        if confidence < 0.5:
            review_reasons.append(f"Low AI confidence score: {confidence:.2f}")

        # AI said review
        if ai_decision == "NEEDS_HUMAN_REVIEW" and not rejection_reasons:
            review_reasons.extend(
                (vision_result or {}).get("human_review_reasons", ["AI flagged for review"])
            )

        # Determine final decision
        if rejection_reasons:
            decision = Decision.REJECTED
        elif review_reasons:
            decision = Decision.NEEDS_HUMAN_REVIEW
        else:
            decision = Decision.APPROVED

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

    def _infer_confidence(self, vision_result: Dict[str, Any]) -> float:
        """
        Derives a confidence score when the model returned 0.0 (unfilled placeholder).
        Scores based on how much usable content the model actually produced.
        """
        score = 0.0
        # Known property type → +0.3
        if vision_result.get("property_type", "unknown") != "unknown":
            score += 0.3
        # Explicit decision → +0.2
        if vision_result.get("decision") in ("APPROVED", "REJECTED", "NEEDS_HUMAN_REVIEW"):
            score += 0.2
        # Non-empty summary → +0.2
        if vision_result.get("summary", "").strip():
            score += 0.2
        # Any non-default observations → +0.1 per flag (cap at +0.3)
        obs = vision_result.get("observations", {})
        flagged = sum(1 for k, v in obs.items() if v is True)
        score += min(0.3, flagged * 0.1)
        return round(min(score, 0.95), 2)

    def _check_residential(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.boarded_windows:
            reasons.append("Boarded windows detected — property may be abandoned or condemned")
        if obs.roof_damage:
            reasons.append("Significant roof damage visible")
        if obs.visible_structure_damage:
            reasons.append("Visible structural damage observed")
        if obs.abandoned_appearance:
            reasons.append("Property appears abandoned")
        if obs.trash_or_debris:
            reasons.append("Severe trash or debris accumulation observed")
        return reasons

    def _check_commercial(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        ct = obs.commercial_type_detected.lower()
        for rejected in REJECTED_COMMERCIAL_TYPES:
            if rejected in ct:
                reasons.append(f"Ineligible commercial property type: {ct}")
                break
        return reasons

    def _check_vacant_land(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.parcel_shape == ParcelShape.NARROW:
            reasons.append("Narrow side lot — typically unbuildable")
        if obs.parcel_shape == ParcelShape.TRIANGLE:
            reasons.append("Triangle shaped lot — typically unbuildable")
        if obs.landlocked:
            reasons.append("Landlocked parcel — no road access")
        if not obs.road_access:
            reasons.append("No visible road access to parcel")
        if obs.wooded:
            reasons.append("Heavily wooded lot — buildability concerns")
        return reasons

    def _check_agriculture(self, obs: PropertyObservations) -> List[str]:
        reasons = []
        if obs.water_body_present and obs.parcel_shape == ParcelShape.IRREGULAR:
            reasons.append("Isolated water body in irregularly shaped agricultural parcel")
        if not obs.road_access:
            reasons.append("Agricultural parcel is inaccessible (no road access)")
        return reasons

    def _parse_observations(self, raw: Dict[str, Any]) -> PropertyObservations:
        try:
            shape_val = raw.get("parcel_shape", "unknown")
            if shape_val not in [s.value for s in ParcelShape]:
                shape_val = "unknown"

            density_val = raw.get("neighborhood_density", "unknown")
            from ..models.property import NeighborhoodDensity
            if density_val not in [d.value for d in NeighborhoodDensity]:
                density_val = "unknown"

            return PropertyObservations(
                boarded_windows=bool(raw.get("boarded_windows", False)),
                roof_damage=bool(raw.get("roof_damage", False)),
                visible_structure_damage=bool(raw.get("visible_structure_damage", False)),
                abandoned_appearance=bool(raw.get("abandoned_appearance", False)),
                trash_or_debris=bool(raw.get("trash_or_debris", False)),
                road_access=bool(raw.get("road_access", True)),
                landlocked=bool(raw.get("landlocked", False)),
                wooded=bool(raw.get("wooded", False)),
                water_body_present=bool(raw.get("water_body_present", False)),
                parcel_shape=shape_val,
                buildable=bool(raw.get("buildable", True)),
                commercial_type_detected=str(raw.get("commercial_type_detected", "none")),
                neighborhood_density=density_val,
            )
        except Exception as e:
            logger.error(f"Observation parse error: {e}")
            return PropertyObservations()

    def _parse_property_type(self, raw: str) -> PropertyType:
        mapping = {
            "residential": PropertyType.RESIDENTIAL,
            "commercial": PropertyType.COMMERCIAL,
            "vacant_land": PropertyType.VACANT_LAND,
            "agriculture": PropertyType.AGRICULTURE,
        }
        return mapping.get(raw.lower(), PropertyType.UNKNOWN)

    def _build_summary(
        self,
        decision: Decision,
        prop_type: PropertyType,
        obs: PropertyObservations,
        rejections: List[str],
        reviews: List[str],
        ai_summary: str,
    ) -> str:
        parts = [f"Property type: {prop_type.value}. Decision: {decision.value}."]
        if rejections:
            parts.append("Rejection reasons: " + "; ".join(rejections) + ".")
        if reviews:
            parts.append("Review flags: " + "; ".join(reviews) + ".")
        if ai_summary:
            parts.append(f"AI analysis: {ai_summary}")
        return " ".join(parts)
