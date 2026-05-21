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

ANALYSIS_PROMPT = """Look at these property images. Image 1 is the satellite/aerial view — the red boundary marks the exact property to evaluate. Images 2-4 are street views.

Answer each question about the property INSIDE the red boundary. Be decisive.

Return ONLY this JSON (no extra text, no markdown):
{
  "property_type": "residential",
  "damaged_or_burned": false,
  "plywood_on_windows": false,
  "heavy_garbage_debris": false,
  "vacancy_signs": false,
  "mobile_home": false,
  "under_construction": false,
  "banned_facility": false,
  "banned_facility_type": "none",
  "has_road_access": true,
  "narrow_strip_lot": false,
  "triangle_lot": false,
  "heavily_wooded": false,
  "pond_on_land": false,
  "has_structure": true,
  "confidence": 0.8,
  "notes": "one sentence describing what you see"
}

Rules for each field:
- property_type: residential / commercial / vacant_land / agriculture / unknown
- damaged_or_burned: true only if structure is visibly collapsed, burned, or severely damaged
- plywood_on_windows: true only for WOOD PLYWOOD boards nailed over windows. Metal hurricane shutters = false.
- heavy_garbage_debris: true only if yard is completely covered in garbage piles or large debris piles
- vacancy_signs: true if property shows clear vacancy — overgrown lawn, no vehicles, broken windows, mail piled up, or general neglect
- mobile_home: true if the structure is a mobile home, manufactured home, or trailer (not a site-built house)
- under_construction: true if structure is clearly mid-construction — visible framing, missing roof, scaffolding, no walls
- banned_facility: true if you see a hospital (not clinic), K-12 school (not preschool), church, mosque, synagogue, temple, gas station, or auto repair shop
- banned_facility_type: exact name of what you see (e.g. "church", "gas station"), or "none"
- has_road_access: false only if the parcel is completely landlocked with no street or road touching it
- narrow_strip_lot: true only if lot is a very thin long strip (like a driveway-sized strip of land)
- triangle_lot: true only if lot is a clearly triangular wedge shape
- heavily_wooded: true only if the entire lot is covered in dense forest/trees with no cleared areas
- pond_on_land: true only for a small isolated water puddle or pond sitting in the middle of land (drainage problem). Pool, lake, canal, ocean = false.
- has_structure: true if there is any house, building, or permanent structure on the parcel
- confidence: 0.9=clear image, 0.7=some uncertainty, 0.5=blurry/unclear, 0.3=cannot determine"""

# ─────────────────────────────────────────────────────────────────────────────
# TEST MODEL: llava (active)
# Production model: minicpm-v (commented out below)
# Switch by swapping which class VisionService points to at the bottom.
# ─────────────────────────────────────────────────────────────────────────────

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
            return None

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

        logger.info(f"[TEST] Sending {len(images_b64)} images to {self.model}")
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
        max_px: int = 640,
    ) -> List[str]:
        """Load and resize images to max_px on longest side before encoding.
        Smaller images = 2-3x faster Ollama inference with no accuracy loss."""
        from PIL import Image as PILImage
        images = []
        for path in [
            satellite_path,
            street_paths.get("center"),
            street_paths.get("left"),
            street_paths.get("right"),
        ]:
            if not path or not Path(path).exists():
                continue
            try:
                img = PILImage.open(path).convert("RGB")
                if max(img.size) > max_px:
                    img.thumbnail((max_px, max_px), PILImage.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=82)
                images.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
            except Exception as e:
                logger.warning(f"Failed to load image {path}: {e}")
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
