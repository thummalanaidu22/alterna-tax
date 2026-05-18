import base64
import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are an AI-powered Property Due Diligence Analyst.

You have been given:
1. A satellite image with parcel boundary overlay (red boundary = target property)
2. Street View Center image
3. Street View Left image
4. Street View Right image

Analyze ONLY the highlighted target parcel (red boundary/marker).

Return ONLY valid JSON matching this exact schema:
{
  "decision": "APPROVED | REJECTED | NEEDS_HUMAN_REVIEW",
  "property_type": "residential | commercial | vacant_land | agriculture | unknown",
  "confidence_score": 0.85,
  "observations": {
    "boarded_windows": false,
    "roof_damage": false,
    "visible_structure_damage": false,
    "abandoned_appearance": false,
    "trash_or_debris": false,
    "road_access": true,
    "landlocked": false,
    "wooded": false,
    "water_body_present": false,
    "parcel_shape": "rectangular | square | narrow | triangle | irregular | unknown",
    "buildable": true,
    "commercial_type_detected": "none",
    "neighborhood_density": "low | medium | high | unknown"
  },
  "rejection_reasons": [],
  "human_review_reasons": [],
  "summary": ""
}

SOP Rules:
REJECT residential if: boarded windows, severe roof damage, abandoned, burned, collapsed, heavy debris.
REJECT commercial if: hospital, church, mosque, synagogue, gas station, auto repair.
REJECT vacant land if: narrow side lot, triangle shaped, landlocked, no road access, heavily wooded.
REJECT agriculture if: isolated pond inside parcel, irregular unusable shape, inaccessible.
APPROVE if property meets standard conditions.
Mark NEEDS_HUMAN_REVIEW if uncertain.

Return ONLY the JSON object, no markdown, no explanation."""

# ─────────────────────────────────────────────────────────────────────────────
# TEST MODEL: llava (active)
# Production model: minicpm-v (commented out below)
# Switch by swapping which class VisionService points to at the bottom.
# ─────────────────────────────────────────────────────────────────────────────

TEST_MODEL = "llava"


class _LlavaVisionService:
    """
    TEST: Uses llava via Ollama /api/generate.
    Drop-in replacement for MiniCPM-V during development.
    """

    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = TEST_MODEL
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
    ) -> List[str]:
        images = []
        for path in [
            satellite_path,
            street_paths.get("center"),
            street_paths.get("left"),
            street_paths.get("right"),
        ]:
            if path and Path(path).exists():
                with open(path, "rb") as f:
                    images.append(base64.b64encode(f.read()).decode("utf-8"))
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


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCTION: MiniCPM-V via Ollama
# Uncomment this class and swap VisionService = _MiniCPMVisionService below
# when ready to switch back.
# ─────────────────────────────────────────────────────────────────────────────

# class _MiniCPMVisionService:
#     """
#     Production vision service using MiniCPM-V via Ollama.
#     Configured via OLLAMA_MODEL env var (default: minicpm-v).
#     """
#
#     def __init__(self):
#         self.base_url = settings.ollama_base_url
#         self.model = settings.ollama_model  # "minicpm-v"
#         self.timeout = settings.ollama_timeout
#
#     async def analyze(
#         self,
#         satellite_path: Optional[str],
#         street_paths: Dict[str, Optional[str]],
#     ) -> Optional[Dict[str, Any]]:
#         images_b64 = self._load_images(satellite_path, street_paths)
#         if not images_b64:
#             logger.error("No images available for vision analysis")
#             return None
#
#         payload = {
#             "model": self.model,
#             "prompt": ANALYSIS_PROMPT,
#             "images": images_b64,
#             "stream": False,
#             "options": {
#                 "temperature": 0.1,
#                 "top_p": 0.9,
#                 "num_predict": 1024,
#             },
#         }
#
#         try:
#             async with httpx.AsyncClient(timeout=self.timeout) as client:
#                 resp = await client.post(
#                     f"{self.base_url}/api/generate", json=payload
#                 )
#                 if resp.status_code == 200:
#                     data = resp.json()
#                     raw = data.get("response", "")
#                     return self._parse_response(raw)
#                 else:
#                     logger.error(f"Ollama error {resp.status_code}: {resp.text}")
#         except httpx.ConnectError:
#             logger.warning("Ollama not reachable — rule-engine fallback only")
#         except Exception as e:
#             logger.error(f"Vision analysis error: {e}")
#
#         return None
#
#     def _load_images(
#         self,
#         satellite_path: Optional[str],
#         street_paths: Dict[str, Optional[str]],
#     ) -> List[str]:
#         images = []
#         for path in [
#             satellite_path,
#             street_paths.get("center"),
#             street_paths.get("left"),
#             street_paths.get("right"),
#         ]:
#             if path and Path(path).exists():
#                 with open(path, "rb") as f:
#                     images.append(base64.b64encode(f.read()).decode("utf-8"))
#         return images
#
#     def _parse_response(self, raw: str) -> Optional[Dict[str, Any]]:
#         raw = raw.strip()
#         match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
#         if match:
#             raw = match.group(1)
#         try:
#             return json.loads(raw)
#         except json.JSONDecodeError:
#             pass
#         match = re.search(r"\{.*\}", raw, re.DOTALL)
#         if match:
#             try:
#                 return json.loads(match.group(0))
#             except json.JSONDecodeError:
#                 pass
#         logger.error(f"Failed to parse vision response: {raw[:200]}")
#         return None
#
#     async def check_ollama_health(self) -> bool:
#         try:
#             async with httpx.AsyncClient(timeout=5) as client:
#                 resp = await client.get(f"{self.base_url}/api/tags")
#                 return resp.status_code == 200
#         except Exception:
#             return False


# ─── Active service binding ───────────────────────────────────────────────────
# TEST:       VisionService = _LlavaVisionService
# PRODUCTION: VisionService = _MiniCPMVisionService
VisionService = _LlavaVisionService
