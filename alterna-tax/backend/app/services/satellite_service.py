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
        self.output_dir = (Path(settings.image_output_dir) / "satellite").resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.zoom = settings.satellite_zoom
        self.size = settings.satellite_image_width

    def _choose_zoom(self, parcel: Dict[str, Any]) -> int:
        """
        Pick satellite zoom level based on parcel size.
          zoom 20 → ~0.15m/px  — residential / small lots (< 30,000 sqft) — maximum detail
          zoom 19 → ~0.30m/px  — medium lots / commercial (30,000–200,000 sqft)
          zoom 18 → ~0.60m/px  — large commercial / multi-acre (200,000–500,000 sqft)
          zoom 17 → ~1.20m/px  — agriculture / large farms (> 500,000 sqft)
        Using zoom 20 as default (from config) for maximum clarity on typical Florida lots.
        """
        area = parcel.get("area_sqft") if parcel else None
        if area is None:
            return self.zoom  # default from config (now 20)
        if area < 50_000:
            return 20  # residential / small lots — maximum detail (0.15m/px)
        if area < 200_000:
            return 19  # medium commercial
        if area < 500_000:
            return 18  # large commercial / multi-acre
        return 17      # agriculture / large farms

    async def capture(
        self,
        lat: float,
        lon: float,
        parcel: Dict[str, Any],
        job_id: str,
    ) -> Optional[str]:
        # Choose zoom based on GIS parcel size (if real) or default for estimated
        zoom = self._choose_zoom(parcel)
        logger.info("Satellite capture: zoom=%d parcel_area=%.0f sqft",
                    zoom, parcel.get("area_sqft", 0) if parcel else 0)

        result = await asyncio.to_thread(self._fetch_esri_tiles, lat, lon, zoom)
        if not result:
            logger.error(f"Failed to fetch ESRI tiles for ({lat},{lon})")
            return None

        img_bytes, actual_zoom = result  # unpack image + zoom actually used
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        # ── Polygon priority (highest accuracy → fallback) ─────────────────────
        # 1. Real Regrid GIS parcel (exact tax parcel boundary)
        # 2. OSM building footprint (UNIQUE per property — fixes same-box-for-all bug)
        # 3. Estimated box scaled to the actual building footprint area
        # 4. Fixed estimated box as absolute last resort
        #
        # CRITICAL: never use an estimated GIS box when OSM building data is available.
        # Estimated boxes are identical for all same-type properties → model sees same
        # pattern → classifies all as the same type.

        gis_parcel   = parcel or {}
        is_estimated = gis_parcel.get("properties", {}).get("estimated", True)

        polygon = None

        # Priority 1: real Regrid polygon (not estimated)
        if not is_estimated:
            polygon = self._extract_parcel_polygon(gis_parcel, lat, lon)
            if polygon:
                logger.info("Polygon: real Regrid GIS boundary (%d pts)", len(polygon))

        # Priority 2: OSM building footprint — ALWAYS try this before estimated box
        # Each building has a unique footprint → model sees different shapes per property
        if not polygon:
            osm_bldg = await asyncio.to_thread(self._query_overpass_polygon, lat, lon)
            if osm_bldg:
                osm_clat = sum(p[0] for p in osm_bldg) / len(osm_bldg)
                osm_clon = sum(p[1] for p in osm_bldg) / len(osm_bldg)
                dist_m   = math.hypot((osm_clat - lat) * 111_320,
                                      (osm_clon - lon) * 111_320 * math.cos(math.radians(lat)))
                if dist_m <= 60:
                    # Scale the building footprint outward to approximate the parcel boundary.
                    # Typical US lot = 3-4× building footprint area. Scale factor 2.0 adds ~40% margin.
                    polygon = self._scale_polygon(osm_bldg, osm_clat, osm_clon, scale=2.0)
                    logger.info("Polygon: OSM building footprint × 2.0 scale (centroid %.0fm away, %d pts)", dist_m, len(polygon))
                else:
                    logger.warning("OSM building %.0fm away — too far, skipping", dist_m)

        # Priority 3: estimated GIS box (same type = same size — last resort only)
        if not polygon:
            polygon = self._extract_parcel_polygon(gis_parcel, lat, lon)
            if polygon:
                logger.info("Polygon: estimated GIS box (no building found — likely vacant/agriculture)")

        self._draw_overlay(img, lat, lon, polygon, actual_zoom)

        out_path = self.output_dir / f"{job_id}_satellite.jpg"
        img.save(str(out_path), "JPEG", quality=85)
        logger.info(f"Satellite image saved: {out_path}")
        return str(out_path.resolve())

    # ── ESRI tile fetching ──────────────────────────────────────────────────────

    def _fetch_single_tile(self, url: str) -> Optional[bytes]:
        """Fetch one ESRI tile with one retry on failure."""
        for attempt in range(2):
            try:
                r = httpx.get(url, headers=HEADERS, timeout=12, follow_redirects=True)
                r.raise_for_status()
                return r.content
            except Exception as e:
                if attempt == 0:
                    logger.debug(f"Tile attempt 1 failed {url}: {e} — retrying")
                else:
                    logger.warning(f"Tile failed after retry {url}: {e}")
        return None

    def _fetch_esri_tiles(self, lat: float, lon: float, zoom: Optional[int] = None) -> Optional[Tuple[bytes, int]]:
        """Fetch tiles at the requested zoom; auto-fallback to zoom-1 if too many tiles fail.
        Returns (image_bytes, zoom_used) so the overlay can use the correct scale."""
        start_zoom = zoom if zoom is not None else self.zoom
        for z in [start_zoom, max(start_zoom - 1, 17)]:
            result = self._fetch_tiles_at_zoom(lat, lon, z)
            if result is not None:
                return result, z
            logger.warning(f"Tiles failed at zoom={z} — retrying at zoom={z - 1}")
        logger.error(f"Satellite tile fetch failed at all zoom levels for ({lat},{lon})")
        return None

    def _fetch_tiles_at_zoom(self, lat: float, lon: float, zoom: int) -> Optional[bytes]:
        ftx, fty = _lat_lon_to_fractional_tile(lat, lon, zoom)
        cx_tile, cy_tile = int(ftx), int(fty)

        canvas = Image.new("RGB", (TILE_PX * GRID, TILE_PX * GRID), (0, 0, 0))

        # Build the full list of (grid_pos, url) pairs then fetch all in parallel
        tile_jobs = []
        for dy in range(-(GRID // 2), GRID // 2 + 1):
            for dx in range(-(GRID // 2), GRID // 2 + 1):
                tx, ty = cx_tile + dx, cy_tile + dy
                url = ESRI_TILE_URL.format(z=zoom, y=ty, x=tx)
                tile_jobs.append((dx, dy, url))

        from concurrent.futures import ThreadPoolExecutor, as_completed
        success_count = 0
        with ThreadPoolExecutor(max_workers=GRID * GRID) as pool:
            futures = {pool.submit(self._fetch_single_tile, url): (dx, dy)
                       for dx, dy, url in tile_jobs}
            for future in as_completed(futures):
                dx, dy = futures[future]
                content = future.result()
                if content:
                    try:
                        tile = Image.open(io.BytesIO(content)).convert("RGB")
                        canvas.paste(tile, ((dx + GRID // 2) * TILE_PX, (dy + GRID // 2) * TILE_PX))
                        success_count += 1
                    except Exception as e:
                        logger.warning(f"Tile decode failed dx={dx} dy={dy}: {e}")

        total = GRID * GRID
        logger.info(f"Tiles loaded: {success_count}/{total} at zoom={zoom}")

        # If less than half the tiles loaded, the image is mostly black — reject it
        if success_count < total // 2:
            logger.warning(f"Too many tile failures ({total - success_count}/{total}) — will retry at lower zoom")
            return None

        # Crop to desired size centred on the property
        # Use round() not int() — floor truncation shifts the center by up to 1px
        frac_x = ftx - cx_tile
        frac_y = fty - cy_tile
        prop_x = round((GRID // 2 + frac_x) * TILE_PX)
        prop_y = round((GRID // 2 + frac_y) * TILE_PX)
        half = self.size // 2
        img = canvas.crop((prop_x - half, prop_y - half, prop_x + half, prop_y + half))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        logger.info(f"ESRI tiles stitched at zoom={zoom} size={self.size}×{self.size}")
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

    def _scale_polygon(
        self,
        polygon: List[Tuple[float, float]],
        center_lat: float,
        center_lon: float,
        scale: float = 2.0,
    ) -> List[Tuple[float, float]]:
        """
        Scale a polygon outward from its centroid by `scale` factor.
        Used to expand an OSM building footprint into an approximate parcel boundary.
        scale=2.0 → parcel is ~2× larger than the building in each direction.
        """
        scaled = []
        for lat, lon in polygon:
            new_lat = center_lat + (lat - center_lat) * scale
            new_lon = center_lon + (lon - center_lon) * scale
            scaled.append((new_lat, new_lon))
        return scaled

    def _extract_parcel_polygon(
        self, parcel: Dict[str, Any], lat: float, lon: float
    ) -> Optional[List[Tuple[float, float]]]:
        """
        Extract the parcel boundary polygon from GIS data and return as (lat, lon) tuples.
        GeoJSON stores coordinates as [lon, lat] — we swap to (lat, lon) for the overlay.
        Returns None if no usable polygon is found.
        """
        if not parcel:
            return None
        geom = parcel.get("geometry", {})
        if not geom:
            return None
        coords_raw = geom.get("coordinates", [])
        if not coords_raw:
            return None
        # Take the outer ring of a Polygon (GeoJSON: coordinates[0] = outer ring)
        ring = coords_raw[0] if isinstance(coords_raw[0][0], list) else coords_raw
        if len(ring) < 3:
            return None
        # GeoJSON is [lon, lat] → swap to (lat, lon) for _draw_overlay
        polygon = [(float(c[1]), float(c[0])) for c in ring]
        # Sanity check: centroid should be close to the target coordinates
        clat = sum(p[0] for p in polygon) / len(polygon)
        clon = sum(p[1] for p in polygon) / len(polygon)
        dist_m = math.hypot((clat - lat) * 111_320,
                            (clon - lon) * 111_320 * math.cos(math.radians(lat)))
        if dist_m > 500:
            logger.warning("GIS polygon centroid is %.0fm away — ignoring (likely wrong parcel)", dist_m)
            return None
        return polygon

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
        zoom: Optional[int] = None,
    ) -> None:
        # Use the actual zoom the image was captured at — critical for correct pixel positions
        effective_zoom = zoom if zoom is not None else self.zoom

        def to_px(lat, lon):
            return _lat_lon_to_pixel(lat, lon, center_lat, center_lon, effective_zoom, self.size)

        # Semi-transparent red fill + solid red outline via RGBA composite
        if polygon and len(polygon) >= 3:
            pixels = [to_px(la, lo) for la, lo in polygon]
            overlay = img.convert("RGBA")
            ov_draw = ImageDraw.Draw(overlay, "RGBA")
            ov_draw.polygon(pixels, fill=(255, 30, 30, 45), outline=(255, 30, 30, 255))
            ov_draw.line(pixels + [pixels[0]], fill=(255, 30, 30, 255), width=3)
            img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

        # Crosshair marker — much more visible than a tiny dot
        draw = ImageDraw.Draw(img)
        cx, cy = self.size // 2, self.size // 2
        arm = 18   # crosshair arm length in pixels
        gap = 5    # gap between center and line start

        # White outline for contrast on any background
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.line([cx + dx + gap, cy + dy, cx + dx + arm, cy + dy], fill=(255, 255, 255), width=3)
            draw.line([cx + dx - arm, cy + dy, cx + dx - gap, cy + dy], fill=(255, 255, 255), width=3)
            draw.line([cx + dx, cy + dy + gap, cx + dx, cy + dy + arm], fill=(255, 255, 255), width=3)
            draw.line([cx + dx, cy + dy - arm, cx + dx, cy + dy - gap], fill=(255, 255, 255), width=3)

        # Red crosshair lines
        draw.line([cx + gap, cy, cx + arm, cy], fill=(220, 20, 20), width=2)
        draw.line([cx - arm, cy, cx - gap, cy], fill=(220, 20, 20), width=2)
        draw.line([cx, cy + gap, cx, cy + arm], fill=(220, 20, 20), width=2)
        draw.line([cx, cy - arm, cx, cy - gap], fill=(220, 20, 20), width=2)

        # Red center dot
        draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255))
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(220, 20, 20))

    def image_to_base64(self, path: str) -> str:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
