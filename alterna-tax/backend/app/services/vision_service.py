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

ANALYSIS_PROMPT = """You are analyzing a property for a real estate investment fund. You have up to 4 images:
- Image 1: Aerial/satellite view. The RED boundary outlines the exact parcel to evaluate.
- Images 2-4: Street-level photos of the same property from different angles (left, center, right).

STEP 1 — DETERMINE PROPERTY TYPE FIRST (most important field):
Look at the aerial and street views carefully. Ask yourself:
  A) Is there a house / townhouse / duplex / condo unit inside the boundary? → "residential"
  B) Is it a store, office, restaurant, strip mall, warehouse, clinic, hotel, apartment complex? → "commercial"
  C) Is the parcel empty land with NO structure — just grass, dirt, trees, or pavement? → "vacant_land"
  D) Is it farmland, grove, ranch, pasture, timberland — large open land used for farming? → "agriculture"
  E) Is the structure clearly a manufactured/mobile home on a chassis or blocks? → "mobile_home"
  F) Is it a factory, warehouse, industrial facility, self-storage, auto yard? → "industrial"
  G) Cannot tell at all? → "unknown"

CRITICAL: Do NOT default to "residential". Look at what is actually there.
  - Empty grass lot with no building = "vacant_land", NOT residential
  - Farm fields, groves, pastures = "agriculture", NOT residential
  - Stores, offices, warehouses = "commercial", NOT residential
  - Mobile/manufactured home = "mobile_home", NOT residential

STEP 2 — Fill in the full JSON. Return ONLY valid JSON, no markdown fences:
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
  "notes": "one sentence describing what you see inside the red boundary"
}

Field rules:
- property_type: MUST be one of: "residential" / "commercial" / "vacant_land" / "agriculture" / "mobile_home" / "industrial" / "unknown"
  Visual cues by type:
    residential  → pitched roof house, single/multi-family home, townhouse, condo, duplex
    commercial   → flat-roof building, storefront, parking lot with business, office, warehouse, apartment complex (5+ units)
    vacant_land  → empty lot, cleared land, raw land, grassy parcel, no structure visible inside boundary
    agriculture  → farm rows, grove trees in rows, pasture/grazing land, ranch, greenhouse, large open rural land
    mobile_home  → rectangular metal/vinyl structure on chassis, visible skirting, not a site-built foundation
    industrial   → large metal building, loading docks, outdoor storage yard, self-storage facility
- damaged_or_burned: true ONLY if structure shows collapse, burn marks, fire damage, or partial demolition. Normal wear=false.
- plywood_on_windows: true ONLY for raw WOOD boards nailed over window openings. Hurricane shutters=false. Blinds=false.
- heavy_garbage_debris: true ONLY if clearly visible MAN-MADE waste: trash bags, junk cars, appliances, rubble piles. Grass/trees/weeds/bushes=false. Natural vegetation=false.
- vacancy_signs: true ONLY if a structure EXISTS AND shows boarded windows + overgrown + no activity ALL together. Vacant lot with no building=false.
- mobile_home: true if clearly a prefabricated manufactured home on blocks/chassis, not a site-built house.
- hurricane_shutters: true if windows/doors are covered by ALUMINUM or METAL accordion/roll-down storm shutters. These are NOT boards — they are metallic, uniform, and fitted to the window frame. false for plywood boards, false for open windows.
- under_construction: true ONLY if exposed framing, missing roof, scaffolding, no exterior finish.
- religious_building_type: "church" / "synagogue" / "mosque" / "temple" / "none"
- commercial_reject_type: "gas_station" / "auto_repair" / "none"
- hospital: true ONLY for large multi-story hospital/ER complex. Small clinic=false.
- k12_school: true ONLY for K-12 campus with playground/portables. Preschool/daycare=false.
- has_road_access: false ONLY if completely landlocked with zero road or driveway touching.
- street_frontage: false ONLY if rear/alley lot with zero public street frontage.
- side_lot: true ONLY if narrow sliver strip with no buildable width.
- triangle_lot: true ONLY if aerial shows a clearly triangular wedge shape.
- lot_size_adequate: false if visibly much smaller than all neighboring lots and unbuildable.
- heavily_wooded: true ONLY if the ENTIRE parcel is dense trees with zero cleared area.
- water_hole_on_land: true ONLY for water-filled depression or wet area INSIDE parcel. Pool=false. Adjacent pond=false.
- parcel_is_parking_only: true if aerial shows the boundary contains ONLY a parking lot with no building.
- has_structure: AERIAL Image 1 ONLY — is there a building footprint (roof shape, rectangle) INSIDE the RED boundary? Default=false. Only true if certain from aerial.
- agri_has_house / agri_fronts_road / agri_shape_regular: agriculture parcels ONLY — set false for all other types.
- confidence: 0.96=crystal clear all fields certain / 0.92=clear images type confirmed / 0.87=minor obstruction / 0.75=uncertainty on 1-2 fields / 0.62=satellite only / 0.45=cannot determine type
  * Clear daytime images where type is obvious → use 0.92 or higher. Do NOT default to 0.75 or 0.88."""

# ── Second-pass prompt (focused re-analysis for low confidence) ───────────────

SECOND_PASS_PROMPT = """Review these property images again. Your previous analysis had low confidence ({prev_conf:.0%}).

STEP 1 — Re-determine the property type. Look at the aerial and street views:
  - Empty lot, grass, raw land, no building inside boundary → "vacant_land"
  - Farm rows, grove, pasture, rural land → "agriculture"
  - Mobile/manufactured home on chassis/blocks → "mobile_home"
  - Store, office, warehouse, apartment complex → "commercial"
  - Factory, industrial building, storage yard → "industrial"
  - Single/multi-family house, condo, townhouse → "residential"
  - Cannot determine → "unknown"
Do NOT default to "residential". Use what you actually see.

CRITICAL RULES:
- heavy_garbage_debris: ONLY man-made waste (trash bags, junk cars, appliances, rubble). Grass/trees/weeds = false.
- vacancy_signs: ONLY if a STRUCTURE exists AND shows boarded windows + overgrown + no activity together. Vacant land = false.
- has_structure: true only if a permanent building footprint is inside the RED boundary on the aerial.

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
  "confidence": 0.80,
  "notes": "clear description of what you see inside the red boundary"
}"""

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

        # Build prompt — prepend type hint when available so the model has context
        prompt = ANALYSIS_PROMPT
        if property_type_hint:
            hint_clean = property_type_hint.strip().lower()
            prompt = (
                f"ZONING HINT FROM COUNTY RECORDS: This parcel is recorded as '{hint_clean}'. "
                f"Use this as strong prior evidence for property_type, but override it if the images clearly show something different.\n\n"
                + prompt
            )
            logger.info("Vision analysis: property_type_hint='%s'", hint_clean)

        # Pass 1 — primary analysis
        result = await self._call_model(prompt, images_b64, temperature=0.1)
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
                "num_predict": 600,   # JSON output rarely exceeds 400 tokens (was 1024)
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
            (satellite_path,             1024, 1.7),  # satellite — 1024px is sufficient for detail
            (street_paths.get("center"), 768,  1.5),  # street views — 768px saves ~35% payload
            (street_paths.get("left"),   768,  1.5),
            (street_paths.get("right"),  768,  1.5),
        ]
        for path, max_px, sharpen in candidates:
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

                # Adaptive local contrast enhancement (CLAHE) via OpenCV when available.
                # CLAHE enhances local regions independently, far better than a fixed global
                # contrast multiplier on satellite/street imagery with mixed lighting zones.
                if _CV2_AVAILABLE:
                    img_np = np.array(img)
                    lab = cv2.cvtColor(img_np, cv2.COLOR_RGB2LAB)
                    l_ch, a_ch, b_ch = cv2.split(lab)
                    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
                    lab = cv2.merge((clahe.apply(l_ch), a_ch, b_ch))
                    img = PILImage.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
                    # Lighter fixed boost after CLAHE since local contrast is already handled
                    img = ImageEnhance.Sharpness(img).enhance(sharpen)
                    img = ImageEnhance.Contrast(img).enhance(1.05)
                    img = ImageEnhance.Brightness(img).enhance(1.05)
                else:
                    # Fallback: original Pillow-only pipeline
                    img = ImageEnhance.Sharpness(img).enhance(sharpen)
                    img = ImageEnhance.Contrast(img).enhance(1.15)
                    img = ImageEnhance.Brightness(img).enhance(1.05)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=95)
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
