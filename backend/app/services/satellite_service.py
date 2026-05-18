"""
Satellite image service.
- Fetches real aerial imagery from ESRI World Imagery tiles (free, no API key).
- Queries OSM Overpass API for building footprint polygon overlay.
- Draws red parcel boundary + red marker pin on the final image.
"""

import asyncio
import base64
import io
import math
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import httpx
from PIL import Image, ImageDraw

from ..config import settings

logger = logging.getLogger(__name__)

TILE_PX = 256
# 5×5 grid = 1280px canvas — the 320px half-crop always fits regardless of sub-tile position
GRID = 5
HEADERS = {"User-Agent": "property-intelligence/1.0"}

ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def _lat_lon_to_fractional_tile(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    tx = (lon + 180.0) / 360.0 * n
    ty = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return tx, ty


def _lat_lon_to_pixel(
    lat: float, lon: float,
    center_lat: float, center_lon: float,
    zoom: int, image_size: int,
) -> Tuple[int, int]:
    cx, cy = _lat_lon_to_fractional_tile(center_lat, center_lon, zoom)
    px, py = _lat_lon_to_fractional_tile(lat, lon, zoom)
    dx = (px - cx) * TILE_PX
    dy = (py - cy) * TILE_PX
    return (int(image_size // 2 + dx), int(image_size // 2 + dy))


class SatelliteService:
    """
    Produces a real satellite image by:
    1. Stitching a 3×3 ESRI World Imagery tile grid centred on the property.
    2. Querying OSM Overpass for the building footprint polygon overlay.
    3. Drawing the red parcel boundary and red pin marker.
    """

    def __init__(self):
        self.output_dir = Path(settings.image_output_dir) / "satellite"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.zoom = settings.satellite_zoom
        self.size = settings.satellite_image_width

    async def capture(
        self,
        lat: float,
        lon: float,
        parcel: Dict[str, Any],
        job_id: str,
    ) -> Optional[str]:
        img_bytes = await asyncio.to_thread(self._fetch_esri_tiles, lat, lon)
        if not img_bytes:
            logger.error(f"Failed to fetch ESRI tiles for ({lat},{lon})")
            return None

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        # Always query OSM for the actual building footprint first.
        # The GIS estimated parcel is a large bounding box (50m+ each side)
        # and MUST NOT be used as the polygon overlay.
        polygon = await asyncio.to_thread(self._query_overpass_polygon, lat, lon)
        if not polygon:
            logger.info("OSM polygon unavailable — using tight estimated footprint")
            polygon = self._estimated_building_footprint(lat, lon)

        self._draw_overlay(img, lat, lon, polygon)

        out_path = self.output_dir / f"{job_id}_satellite.jpg"
        img.save(str(out_path), "JPEG", quality=85)
        logger.info(f"Satellite image saved: {out_path}")
        return str(out_path)

    # ── ESRI tile fetching ──────────────────────────────────────────────────────

    def _fetch_esri_tiles(self, lat: float, lon: float) -> Optional[bytes]:
        ftx, fty = _lat_lon_to_fractional_tile(lat, lon, self.zoom)
        cx_tile, cy_tile = int(ftx), int(fty)

        canvas = Image.new("RGB", (TILE_PX * GRID, TILE_PX * GRID), (0, 0, 0))

        for dy in range(-(GRID // 2), GRID // 2 + 1):
            for dx in range(-(GRID // 2), GRID // 2 + 1):
                tx, ty = cx_tile + dx, cy_tile + dy
                url = ESRI_TILE_URL.format(z=self.zoom, y=ty, x=tx)
                try:
                    r = httpx.get(url, headers=HEADERS, timeout=10, follow_redirects=True)
                    r.raise_for_status()
                    tile = Image.open(io.BytesIO(r.content)).convert("RGB")
                    canvas.paste(tile, ((dx + GRID // 2) * TILE_PX, (dy + GRID // 2) * TILE_PX))
                except Exception as e:
                    logger.warning(f"Tile failed z={self.zoom} tx={tx} ty={ty}: {e}")

        # Crop to desired size centred on the property
        frac_x = ftx - cx_tile
        frac_y = fty - cy_tile
        prop_x = int((GRID // 2 + frac_x) * TILE_PX)
        prop_y = int((GRID // 2 + frac_y) * TILE_PX)
        half = self.size // 2
        img = canvas.crop((prop_x - half, prop_y - half, prop_x + half, prop_y + half))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        logger.info(f"ESRI tiles stitched at zoom={self.zoom} size={self.size}×{self.size}")
        return buf.read()

    # ── OSM Overpass polygon ────────────────────────────────────────────────────

    def _query_overpass_polygon(
        self, lat: float, lon: float
    ) -> Optional[List[Tuple[float, float]]]:
        """
        Queries OSM Overpass for the single closest building footprint within 50m.
        Uses a tight 50m radius so we get the target house, not neighbours.
        """
        query = (
            f"[out:json][timeout:15];"
            f"(way[\"building\"](around:50,{lat},{lon}););"
            f"out body;>;out skel qt;"
        )
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                resp = httpx.post(endpoint, data={"data": query}, headers=HEADERS, timeout=20)
                resp.raise_for_status()
                data = resp.json()

                nodes = {
                    n["id"]: (n["lat"], n["lon"])
                    for n in data.get("elements", [])
                    if n["type"] == "node"
                }
                ways = [e for e in data.get("elements", []) if e["type"] == "way"]
                if not ways:
                    logger.info(f"No OSM building within 50m at ({lat},{lon})")
                    return None

                def centroid_dist(way):
                    pts = [nodes[nid] for nid in way["nodes"] if nid in nodes]
                    if not pts:
                        return float("inf")
                    return math.hypot(
                        sum(p[0] for p in pts) / len(pts) - lat,
                        sum(p[1] for p in pts) / len(pts) - lon,
                    )

                best = min(ways, key=centroid_dist)
                polygon = [nodes[nid] for nid in best["nodes"] if nid in nodes]
                logger.info(f"OSM building polygon: {len(polygon)} pts via {endpoint}")
                return polygon

            except Exception as e:
                logger.warning(f"Overpass failed ({endpoint}): {e}")

        logger.warning("All Overpass endpoints failed")
        return None

    def _estimated_building_footprint(
        self, lat: float, lon: float
    ) -> List[Tuple[float, float]]:
        """
        Fallback: a tight ~12m × 12m estimated building footprint.
        Typical US residential house is 1200–2000 sq ft / ~35×35 ft / ~11m side.
        Much tighter than the GIS parcel bounding box.
        """
        d_lat = 0.000055   # ~6m half-side in latitude  → 12m total
        d_lon = 0.000070   # ~6m half-side in longitude → 12m total
        return [
            (lat + d_lat, lon - d_lon),
            (lat + d_lat, lon + d_lon),
            (lat - d_lat, lon + d_lon),
            (lat - d_lat, lon - d_lon),
            (lat + d_lat, lon - d_lon),  # close ring
        ]

    # ── Overlay drawing ─────────────────────────────────────────────────────────

    def _draw_overlay(
        self,
        img: Image.Image,
        center_lat: float,
        center_lon: float,
        polygon: Optional[List[Tuple[float, float]]],
    ) -> None:
        def to_px(lat, lon):
            return _lat_lon_to_pixel(lat, lon, center_lat, center_lon, self.zoom, self.size)

        # Semi-transparent red fill + solid red outline via RGBA composite
        if polygon and len(polygon) >= 3:
            pixels = [to_px(la, lo) for la, lo in polygon]
            overlay = img.convert("RGBA")
            ov_draw = ImageDraw.Draw(overlay, "RGBA")
            ov_draw.polygon(pixels, fill=(255, 30, 30, 35), outline=(255, 30, 30, 255))
            ov_draw.line(pixels + [pixels[0]], fill=(255, 30, 30, 255), width=2)
            img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

        # Small red dot marker — minimal footprint so building stays visible
        draw = ImageDraw.Draw(img)
        cx, cy = self.size // 2, self.size // 2
        draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(255, 255, 255))
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(220, 20, 20))

    def image_to_base64(self, path: str) -> str:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
