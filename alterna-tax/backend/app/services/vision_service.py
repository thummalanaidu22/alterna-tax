import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    logger.info("OpenCV not available — using Pillow-only image enhancement")

# ── Primary prompt ─────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """You are analyzing a Florida real estate property. You have satellite + street view images.
Images: [1]=Aerial satellite with RED CROSSHAIR marking the property. [2-4]=Street views of the same property.

━━━ STEP 1: ANSWER THESE QUESTIONS FIRST (look at each image before answering) ━━━

Q1. AERIAL IMAGE — look at the RED CROSSHAIR location:
    • Is there a building rooftop (rectangular shape, roof texture) visible at/near the crosshair? YES / NO
    • If YES — is the roof PITCHED/SLOPED (residential) or FLAT/LARGE (commercial)?
    • Is the area around the crosshair bare open land with no structures? YES / NO
    • Are there farm field rows, groves, or agricultural crop patterns? YES / NO

Q2. STREET VIEW IMAGES (if available — these are MORE RELIABLE than aerial):
    • Does the street view show a HOUSE (front door, windows, lawn, driveway)? YES / NO
    • Does the street view show a BUSINESS (storefront, sign, large glass windows, parking lot)? YES / NO
    • Does the street view show an EMPTY LOT (just grass, dirt, fence with nothing built)? YES / NO
    • Does the street view show a FARM or RURAL LAND (fields, fences, agricultural equipment)? YES / NO
    • Does the street view show a MOBILE HOME (metal siding, on blocks, rectangular structure on trailer chassis)? YES / NO

Q3. DETERMINE PROPERTY TYPE from your answers above:
    • Q2=house OR (Q1=pitched roof AND residential neighborhood) → "residential"
    • Q2=business OR Q1=flat large roof with parking → "commercial"
    • Q1=bare land AND Q2=empty lot → "vacant_land"
    • Q1=farm patterns OR Q2=farm/rural → "agriculture"
    • Q2=mobile home → "mobile_home"
    • Cannot see anything clearly → "unknown"

━━━ STEP 2: FILL IN JSON (based ONLY on your answers above) ━━━
Return ONLY valid JSON, no markdown:

{
  "property_type": "unknown",
  "damaged_or_burned": false,
  "plywood_on_windows": false,
  "heavy_garbage_debris": false,
  "vacancy_signs": false,
  "mobile_home": false,
  "hurricane_shutters": false,
  "under_construction": false,
  "religious_building_type": "none",
  "commercial_reject_type": "none",
  "hospital": false,
  "k12_school": false,
  "has_road_access": true,
  "street_frontage": true,
  "side_lot": false,
  "triangle_lot": false,
  "lot_size_adequate": true,
  "heavily_wooded": false,
  "water_hole_on_land": false,
  "parcel_is_parking_only": false,
  "has_structure": false,
  "agri_has_house": false,
  "agri_fronts_road": false,
  "agri_shape_regular": false,
  "confidence": 0.88,
  "notes": "one sentence describing what you see at the crosshair location"
}

Field rules — base each field on your answers from Step 1:
- property_type: Use Q3 result above. MUST be one of:
    "residential" / "commercial" / "vacant_land" / "agriculture" / "mobile_home" / "industrial" / "unknown"
- CRITICAL: Each property is different. Residential looks different from commercial. If street views show a house → residential. If empty lot → vacant_land. Look carefully at EACH property individually.
- damaged_or_burned: true ONLY if structure shows collapse, burn marks, fire damage, partial demolition.
- plywood_on_windows: true ONLY for raw WOOD boards nailed over windows. Hurricane shutters=false.
- heavy_garbage_debris: true ONLY if clearly visible MAN-MADE waste: trash bags, junk cars, appliances, rubble. Grass/trees/weeds=false.
- vacancy_signs: true ONLY if structure EXISTS AND shows boarded windows + overgrown + no activity ALL together.
- mobile_home: true if clearly a prefabricated manufactured home on blocks/chassis.
- hurricane_shutters: true if ALUMINUM or METAL accordion/roll-down storm shutters cover windows. NOT wood boards.
- under_construction: true ONLY if exposed framing, missing roof, scaffolding, no exterior finish.
- religious_building_type: "church" / "synagogue" / "mosque" / "temple" / "none"
- commercial_reject_type: "gas_station" / "auto_repair" / "none"
- hospital: true ONLY for large multi-story hospital/ER complex. Small clinic=false.
- k12_school: true ONLY for K-12 campus with playground/portables. Preschool/daycare=false.
- has_road_access: false ONLY if completely landlocked with zero road or driveway access.
- street_frontage: false ONLY if rear/alley lot with zero public street frontage.
- side_lot: true ONLY if narrow sliver strip with no buildable width.
- triangle_lot: true ONLY if aerial shows clearly triangular wedge-shaped lot.
- lot_size_adequate: false if visibly much smaller than all neighboring lots.
- heavily_wooded: true ONLY if the ENTIRE area near the crosshair is dense trees with zero cleared area.
- water_hole_on_land: true ONLY for water-filled depression INSIDE/near the parcel. Pool=false. Adjacent pond=false.
- parcel_is_parking_only: true if the entire area near crosshair is ONLY a parking lot with no building.
- has_structure: Look at the RED CROSSHAIR area in Image 1. Is there a building rooftop (roof shape, rectangle, structure) visible AT or NEAR the crosshair, OR inside the red boundary? Also check street views — if street views show a clear building at this address, set true. Default=false ONLY if no building visible anywhere near the crosshair in any image.
- agri_has_house / agri_fronts_road / agri_shape_regular: agriculture parcels ONLY.
- confidence: your certainty in the decision. Use this exact scale:
    0.95 = satellite + 2-3 street views, all fields answered with certainty
    0.90 = satellite + 1 street view, property type and condition clearly confirmed
    0.82 = satellite only, property type is CLEARLY visible (house, empty lot, farm, store) — USE THIS for most satellite-only cases
    0.72 = satellite only, some uncertainty on 1 field (condition or exact type)
    0.60 = satellite only, genuinely unclear on multiple fields
    0.45 = cannot determine property type at all from images
  RULES:
    * If you can clearly see the property type from the satellite image → use 0.82 or higher
    * DO NOT use 0.62 or lower unless the images are genuinely blurry, dark, or show nothing identifiable
    * Most clear daytime satellite images of Florida properties should get 0.82+
    * Only use low confidence (< 0.60) if you truly cannot make a determination"""

# ── Second-pass prompt (focused re-analysis for low confidence) ───────────────

SECOND_PASS_PROMPT = """Review these property images again. Your previous analysis had low confidence ({prev_conf:.0%}).

STEP 1 — Re-determine the property type. The RED CROSSHAIR marks the target location.
  Look at the crosshair area + street views to determine:
  - House/building visible near the crosshair in ANY image → NOT vacant_land
  - Farm/ranch/pasture/grove → "agriculture" (not vacant_land)
  - Empty land with NO structure in ANY image → "vacant_land"
  - Mobile/manufactured home → "mobile_home"
  - Store/office/warehouse → "commercial"
  - Factory/industrial → "industrial"
  - Site-built house → "residential"

CRITICAL RULES:
- heavy_garbage_debris: ONLY man-made waste (trash bags, junk cars, rubble). Grass/weeds = false.
- vacancy_signs: ONLY if structure EXISTS AND boarded + overgrown + no activity ALL together. Empty land = false.
- has_structure: Is there a building visible at/near the crosshair in the aerial OR in street views? If street views show a house/building, set true even if aerial boundary looks empty.

Return ONLY valid JSON:
{
  "property_type": "unknown",
  "has_structure": false,
  "damaged_or_burned": false,
  "plywood_on_windows": false,
  "heavy_garbage_debris": false,
  "vacancy_signs": false,
  "has_road_access": true,
  "street_frontage": true,
  "heavily_wooded": false,
  "water_hole_on_land": false,
  "confidence": 0.82,
  "notes": "clear description of what you see inside the red boundary"
}
CONFIDENCE: If you can now clearly see the property type → use 0.82+. Only go below 0.70 if still genuinely unclear."""

# ── Targeted risk-flag verification prompt ────────────────────────────────────

RISK_VERIFICATION_PROMPT = """Re-examine these property images. A previous analysis flagged: {flag_list}.

Look carefully at the images and verify each flagged item. Be precise — do not guess.

STRICT definitions for each possible flag:
- heavy_garbage_debris → ONLY visible MAN-MADE waste items: trash bags, junk/abandoned vehicles, appliances, construction rubble piles. Grass/weeds/trees/bushes/overgrown vegetation = NOT debris. If you only see natural vegetation → false.
- damaged_or_burned → ONLY structural collapse, visible burn/char marks, fire blackening, missing walls, partial demolition. Peeling paint, weathering, age stains = false.
- plywood_on_windows → ONLY raw wood boards physically nailed over window openings. Metal shutters, closed blinds, curtains, roller shutters = false.
- vacancy_signs → ONLY when a BUILDING is present AND you simultaneously see: boarded windows + overgrown lawn + zero signs of activity. Vacant land (no building) = false. One sign alone = false.
- mobile_home → ONLY a clearly prefabricated home on wheels or concrete blocks, visually distinct from a site-built house with a foundation.
- under_construction → ONLY exposed structural framing, missing roof sections, or active scaffolding visible. Renovation with walls intact = false.
- side_lot → ONLY a narrow sliver strip clearly too narrow to build on.
- triangle_lot → ONLY a clearly wedge/triangular shaped lot visible in aerial.
- heavily_wooded → ONLY if the ENTIRE parcel interior is covered in dense trees with absolutely no cleared area.
- water_hole_on_land → ONLY a water-filled depression INSIDE the parcel. Pool = false. Pond adjacent to parcel = false.

Return ONLY valid JSON for the flagged fields:
{{{json_fields},
  "confidence": 0.92,
  "verification_notes": "what you specifically see or do NOT see for each flagged item"
}}"""

# Risk flags: if either pass flagged it, keep it flagged (conservative/safe)
_RISK_FLAGS = [
    "damaged_or_burned", "plywood_on_windows", "heavy_garbage_debris",
    "vacancy_signs", "mobile_home", "under_construction",
    "hospital", "k12_school", "water_hole_on_land", "parcel_is_parking_only",
    "side_lot", "triangle_lot", "heavily_wooded",
]
# Access/positive flags: only true when BOTH passes agree it's true
_POSITIVE_FLAGS = ["has_road_access", "street_frontage", "lot_size_adequate", "has_structure"]

_LOW_CONFIDENCE_THRESHOLD = 0.75

# Flags eligible for targeted re-verification.
# Only include flags that are (a) prone to false positives AND (b) cause outright rejection.
# Minor/borderline flags (side_lot, triangle_lot, heavily_wooded) are deterministic enough
# from the aerial — skipping verification for them saves one full Ollama round-trip each.
_VERIFY_FLAGS = [
    "heavy_garbage_debris", "damaged_or_burned", "plywood_on_windows", "vacancy_signs",
]


class _VisionService:
    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.timeout = settings.ollama_timeout

    async def analyze(
        self,
        satellite_path: Optional[str],
        street_paths: Dict[str, Optional[str]],
        property_type_hint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        images_b64 = self._load_images(satellite_path, street_paths)
        if not images_b64:
            logger.error("No images available for vision analysis")
            return {"_no_images": True}

        # Vision model MUST determine property type from images only.
        # Never pass the property_type_hint here — it locks entire batches to one category
        # (e.g. Alterna "Resi filter" batch would make everything classify as residential).
        # The hint is used as a last-resort fallback in the rule engine only.
        prompt = ANALYSIS_PROMPT

        # Pass 1 — temperature=0.2 balances consistency with flexibility.
        # temperature=0.0 amplified model bias (same type for all properties in a batch).
        result = await self._call_model(prompt, images_b64, temperature=0.2)
        if result is None:
            return None

        # Step 1: fix logical contradictions before any further processing
        result = self._fix_contradictions(result)

        # Step 2: low confidence → full second pass
        conf = float(result.get("confidence", result.get("confidence_score", 0.0)))
        if conf < _LOW_CONFIDENCE_THRESHOLD:
            logger.info("Low confidence (%.0f%%) — running second-pass analysis", conf * 100)
            second = await self._call_model(
                SECOND_PASS_PROMPT.format(prev_conf=conf),
                images_b64,
                temperature=0.0,
            )
            if second:
                result = self._merge(result, second)
                result = self._fix_contradictions(result)
                logger.info(
                    "Second pass complete — confidence %s → %s",
                    f"{conf:.0%}",
                    f"{float(result.get('confidence', 0)):.0%}",
                )

        # Step 3: targeted verification for any True risk flags (anti-false-positive)
        result = await self._verify_risk_flags(result, images_b64)

        return result

    async def check_ollama_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    # ── Private ───────────────────────────────────────────────────────────────

    async def _verify_risk_flags(
        self,
        result: Dict[str, Any],
        images_b64: List[str],
    ) -> Dict[str, Any]:
        """Re-examine any True risk flags with a targeted prompt to eliminate false positives."""
        flagged = {f: True for f in _VERIFY_FLAGS if result.get(f) is True}
        if not flagged:
            return result

        flag_list = ", ".join(flagged.keys())
        json_fields = "\n  ".join(f'"{f}": true' for f in flagged)

        prompt = RISK_VERIFICATION_PROMPT.format(
            flag_list=flag_list,
            json_fields=json_fields,
        )
        logger.info(
            "Running risk-flag verification for: %s",
            flag_list,
        )
        verification = await self._call_model(prompt, images_b64, temperature=0.0)
        if not verification:
            return result

        # Override each flagged field with the verification result
        # (verification is specifically designed to catch false positives)
        for flag in flagged:
            if flag in verification:
                if result.get(flag) != verification[flag]:
                    logger.info(
                        "Flag corrected by verification: %s %s → %s",
                        flag, result.get(flag), verification[flag],
                    )
                result[flag] = verification[flag]

        # Average confidence
        c1 = float(result.get("confidence", 0.5))
        c2 = float(verification.get("confidence", 0.5))
        result["confidence"] = round((c1 + c2) / 2, 2)

        # Append verification notes
        v_notes = str(verification.get("verification_notes", "")).strip()
        if v_notes:
            existing = str(result.get("notes", "")).strip()
            result["notes"] = f"{existing} | Verified: {v_notes}" if existing else v_notes

        return result

    async def _call_model(
        self,
        prompt: str,
        images_b64: List[str],
        temperature: float = 0.1,
    ) -> Optional[Dict[str, Any]]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": images_b64,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
                "num_predict": 1200,  # JSON + notes can exceed 600 tokens — truncation causes parse failures
                "num_ctx": 4096,      # explicit context window — prevents model auto-sizing large
            },
        }
        logger.info("Sending %d images to %s (temp=%.1f)", len(images_b64), self.model, temperature)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/generate", json=payload)
                if resp.status_code == 200:
                    return self._parse_response(resp.json().get("response", ""))
                logger.error("Ollama error %s: %s", resp.status_code, resp.text)
        except httpx.ConnectError:
            logger.warning("Ollama not reachable — rule-engine fallback only")
        except Exception as e:
            logger.error("Vision analysis error: %s", e)
        return None

    def _load_images(
        self,
        satellite_path: Optional[str],
        street_paths: Dict[str, Optional[str]],
    ) -> List[str]:
        from PIL import Image as PILImage, ImageEnhance

        images: List[str] = []
        candidates = [
            (satellite_path,             1024, 2.0, True),   # satellite: sharper detail needed
            (street_paths.get("center"), 960,  1.8, False),  # street center: larger + sharper
            (street_paths.get("left"),   896,  1.6, False),
            (street_paths.get("right"),  896,  1.6, False),
        ]
        for path, max_px, sharpen, is_satellite in candidates:
            if not path:
                continue
            p = Path(path)
            if not p.exists():
                logger.warning("Image not found, skipping: %s", path)
                continue
            try:
                img = PILImage.open(p).convert("RGB")
                if max(img.size) > max_px:
                    img.thumbnail((max_px, max_px), PILImage.LANCZOS)

                if _CV2_AVAILABLE:
                    img_np = np.array(img)
                    lab = cv2.cvtColor(img_np, cv2.COLOR_RGB2LAB)
                    l_ch, a_ch, b_ch = cv2.split(lab)
                    # Stronger CLAHE for satellite (smaller tiles = more local contrast)
                    clip   = 4.0 if is_satellite else 3.0
                    tile   = (4, 4) if is_satellite else (6, 6)
                    clahe  = cv2.createCLAHE(clipLimit=clip, tileGridSize=tile)
                    lab    = cv2.merge((clahe.apply(l_ch), a_ch, b_ch))
                    img    = PILImage.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
                    # Unsharp mask for crisp edges (critical for roof/structure detection)
                    if is_satellite:
                        img_np2 = np.array(img)
                        blur    = cv2.GaussianBlur(img_np2, (0, 0), 2.0)
                        img_np2 = cv2.addWeighted(img_np2, 1.5, blur, -0.5, 0)
                        img     = PILImage.fromarray(np.clip(img_np2, 0, 255).astype(np.uint8))
                    img = ImageEnhance.Sharpness(img).enhance(sharpen)
                    img = ImageEnhance.Contrast(img).enhance(1.15 if is_satellite else 1.10)
                    img = ImageEnhance.Brightness(img).enhance(1.08)
                else:
                    from PIL import ImageFilter
                    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
                    img = ImageEnhance.Sharpness(img).enhance(sharpen)
                    img = ImageEnhance.Contrast(img).enhance(1.20)
                    img = ImageEnhance.Brightness(img).enhance(1.08)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=97)
                images.append(base64.b64encode(buf.getvalue()).decode())
            except Exception as e:
                logger.warning("Failed to load image %s: %s", path, e)

        street_count = len(images) - (1 if satellite_path and images else 0)
        logger.info(
            "Loaded %d images (1 satellite + %d street views) | CLAHE=%s",
            len(images),
            max(0, street_count),
            _CV2_AVAILABLE,
        )
        return images

    def _parse_response(self, raw: str) -> Optional[Dict[str, Any]]:
        raw = raw.strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            raw = m.group(1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        logger.error("Failed to parse vision response: %s", raw[:200])
        return None

    @staticmethod
    def _fix_contradictions(result: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-correct logically impossible field combinations before the rule engine sees them."""
        prop_type = str(result.get("property_type", "")).lower()
        has_structure = result.get("has_structure", True)

        # vacancy_signs requires an existing building — a vacant lot cannot be "vacant"
        if prop_type == "vacant_land" or not has_structure:
            if result.get("vacancy_signs"):
                logger.info("Contradiction fixed: vacancy_signs=true on a lot with no structure → false")
            result["vacancy_signs"] = False

        # No structure means no window boards, no fire damage, no construction
        if not has_structure:
            result["plywood_on_windows"] = False
            result["damaged_or_burned"] = False
            result["under_construction"] = False

        # A parking-only lot has no structure and cannot have vacancy signs
        if result.get("parcel_is_parking_only"):
            result["has_structure"] = False
            result["vacancy_signs"] = False

        # Agriculture parcels are not parking lots; they always have road context
        if prop_type == "agriculture":
            result["parcel_is_parking_only"] = False

        # Commercial + no structure confirmed in aerial → do NOT silently assume parking.
        # Flag for human review so the rule engine can decide correctly.
        if prop_type == "commercial" and not has_structure and not result.get("parcel_is_parking_only"):
            result["_force_human_review"] = True
            result["_review_reason"] = "Commercial type detected but no structure confirmed in aerial — verify property type"
            logger.info("Contradiction: commercial + no structure → flagged for human review")

        return result

    @staticmethod
    def _merge(r1: Dict[str, Any], r2: Dict[str, Any]) -> Dict[str, Any]:
        """Merge two vision results conservatively — prefer the pessimistic reading."""
        merged = {**r1, **r2}  # r2 wins on neutral fields

        # Risk flags: true if EITHER pass flagged it
        for flag in _RISK_FLAGS:
            if r1.get(flag) or r2.get(flag):
                merged[flag] = True

        # Positive flags: only true if BOTH passes agreed
        for flag in _POSITIVE_FLAGS:
            if not r1.get(flag, True) or not r2.get(flag, True):
                merged[flag] = False

        # Average the two confidence scores
        c1 = float(r1.get("confidence", 0.5))
        c2 = float(r2.get("confidence", 0.5))
        merged["confidence"] = round((c1 + c2) / 2, 2)

        # Combine notes
        n1 = str(r1.get("notes", "")).strip()
        n2 = str(r2.get("notes", "")).strip()
        if n1 and n2 and n1 != n2:
            merged["notes"] = f"{n2} | Re-analysis: {n1}"

        return merged


VisionService = _VisionService
