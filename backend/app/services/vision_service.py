import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are analyzing a property for a real estate investment fund. You have up to 4 images:
- Image 1: Aerial/satellite view. The RED boundary outlines the exact parcel to evaluate.
- Images 2-4: Street-level photos of the same property from different angles (left, center, right).

Use ALL available images together. The aerial gives shape/footprint; street views reveal physical condition.
Focus ONLY on the property inside the red boundary — ignore neighboring parcels.

Return ONLY valid JSON with no extra text or markdown fences:
{
  "property_type": "residential",
  "damaged_or_burned": false,
  "plywood_on_windows": false,
  "heavy_garbage_debris": false,
  "vacancy_signs": false,
  "mobile_home": false,
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
  "has_structure": true,
  "agri_has_house": false,
  "agri_fronts_road": false,
  "agri_shape_regular": false,
  "confidence": 0.88,
  "notes": "one sentence describing what you see"
}

Field rules — read carefully:
- property_type: "residential" / "commercial" / "vacant_land" / "agriculture" / "mobile_home" / "industrial" / "unknown"
  * Use street views to confirm: a house=residential, strip mall=commercial, empty lot=vacant_land
- damaged_or_burned: true ONLY if structure shows collapse, burn marks, fire damage, or partial demolition. Normal wear=false.
- plywood_on_windows: true ONLY for raw WOOD boards nailed over window openings. Aluminum hurricane shutters=false. Blinds=false.
- heavy_garbage_debris: true ONLY if the yard/lot has large piles of trash, junk cars, or construction debris everywhere. Neat yard=false.
- vacancy_signs: true if MULTIPLE signs present: boarded windows + overgrown lawn + no activity. One sign alone=false.
- mobile_home: true if structure is clearly a prefabricated mobile/manufactured home on wheels or blocks, NOT a site-built house.
- under_construction: true ONLY if exposed framing, missing roof sections, scaffolding, no exterior finish visible.
- religious_building_type: "church" / "synagogue" / "mosque" / "temple" / "none" — only if you can clearly identify it
- commercial_reject_type: "gas_station" / "auto_repair" / "none" — gas canopies, pumps=gas_station; lift bays, tire racks=auto_repair
- hospital: true ONLY for a large multi-story hospital with ER/medical complex. A small clinic or doctor office=false.
- k12_school: true ONLY for a K-12 school campus with playground, portables, or large parking. Preschool/daycare=false.
- has_road_access: false ONLY if parcel is completely surrounded by other parcels with NO touching road or driveway.
- street_frontage: false ONLY if the parcel is a rear lot or alley lot with zero frontage on a public street.
- side_lot: true ONLY if lot is a narrow sliver strip (like a driveway width) with no buildable frontage.
- triangle_lot: true ONLY if the aerial shows a clearly triangular wedge-shaped lot.
- lot_size_adequate: false if the parcel is visibly much smaller than neighboring lots and appears unbuildable.
- heavily_wooded: true ONLY if the ENTIRE parcel is covered in dense trees with no cleared area visible.
- water_hole_on_land: true ONLY for a small water-filled depression or saturated area INSIDE the parcel. Swimming pool=false. Pond next to parcel=false.
- parcel_is_parking_only: true if aerial shows the parcel boundary contains ONLY a parking lot with no building footprint inside.
- has_structure: true if any permanent building, house, or structure exists on the parcel.
- agri_has_house / agri_fronts_road / agri_shape_regular: fill these ONLY for agriculture parcels; set false for all others.
- confidence: your certainty across ALL fields combined. Use this scale:
    0.93 = crystal-clear satellite + 2-3 street views; property type, condition, and lot shape all unambiguous
    0.88 = clear satellite + at least 1 clear street view; property type and structural condition confidently identified
    0.82 = one image is partially obstructed or slightly blurry but main features still identifiable
    0.72 = significant uncertainty on 1-2 fields; one image missing or very blurry
    0.60 = only satellite available OR street view completely missed the property
    0.45 = cannot determine property type or condition from available images
  IMPORTANT RULES for confidence:
    * If satellite clearly shows the parcel AND at least 1 street view confirms property type → set confidence >= 0.85
    * If you can confidently answer ALL boolean fields (not just guessing) → add 0.04 to your base score
    * Do NOT default to 0.75 out of habit — evaluate each image set on its actual quality"""

PROD_MODEL = "minicpm-v"


class _MiniCPMVisionService:
    """
    Production vision service using MiniCPM-V via Ollama.
    Significantly more accurate than llava for property image analysis.
    """

    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = PROD_MODEL
        self.timeout = settings.ollama_timeout

    async def analyze(
        self,
        satellite_path: Optional[str],
        street_paths: Dict[str, Optional[str]],
    ) -> Optional[Dict[str, Any]]:
        images_b64 = self._load_images(satellite_path, street_paths)
        if not images_b64:
            logger.error("No images available for vision analysis")
            return {"_no_images": True}

        payload = {
            "model": self.model,
            "prompt": ANALYSIS_PROMPT,
            "images": images_b64,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_predict": 1024,
            },
        }

        logger.info(f"Sending {len(images_b64)} images to {self.model}")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate", json=payload
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data.get("response", "")
                    return self._parse_response(raw)
                else:
                    logger.error(f"Ollama error {resp.status_code}: {resp.text}")
        except httpx.ConnectError:
            logger.warning("Ollama not reachable — rule-engine fallback only")
        except Exception as e:
            logger.error(f"Vision analysis error: {e}")

        return None

    def _load_images(
        self,
        satellite_path: Optional[str],
        street_paths: Dict[str, Optional[str]],
    ) -> List[str]:
        """Load, sharpen, and resize images before encoding.
        Higher resolution and sharpening give the model more detail → higher confidence."""
        from PIL import Image as PILImage, ImageEnhance, ImageFilter
        images = []
        paths_and_sizes = [
            (satellite_path,                  1024, 1.4),  # satellite: larger + sharper
            (street_paths.get("center"), 800, 1.3),
            (street_paths.get("left"),   800, 1.3),
            (street_paths.get("right"),  800, 1.3),
        ]
        for path, max_px, sharpen_factor in paths_and_sizes:
            if not path:
                continue
            p = Path(path)
            if not p.exists():
                logger.warning(f"Image not found, skipping: {path}")
                continue
            try:
                img = PILImage.open(p).convert("RGB")
                if max(img.size) > max_px:
                    img.thumbnail((max_px, max_px), PILImage.LANCZOS)
                # Sharpen to bring out edges and details the model needs
                img = ImageEnhance.Sharpness(img).enhance(sharpen_factor)
                # Slight contrast boost so features stand out
                img = ImageEnhance.Contrast(img).enhance(1.1)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=90)
                images.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
            except Exception as e:
                logger.warning(f"Failed to load image {path}: {e}")
        street_count = len(images) - (1 if satellite_path and images else 0)
        logger.info(f"Loaded {len(images)} images for vision analysis (1 satellite + {max(0, street_count)} street views)")
        return images

    def _parse_response(self, raw: str) -> Optional[Dict[str, Any]]:
        raw = raw.strip()
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            raw = match.group(1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.error(f"Failed to parse vision response: {raw[:200]}")
        return None

    async def check_ollama_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False


VisionService = _MiniCPMVisionService
