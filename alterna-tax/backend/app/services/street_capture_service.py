"""
Street View capture service using Playwright browser automation.

Navigates to Google Maps Street View, captures three discrete frames:
  LEFT   — center_heading − SIDE_OFFSET_DEG
  CENTER — center_heading  (pointing toward the property)
  RIGHT  — center_heading + SIDE_OFFSET_DEG

The center heading is the compass bearing from the street camera position to
the property centroid (derived from the OSM/GIS polygon centroid).

Each frame has a crosshair/diamond marker composited at the property's
computed screen-space position.
"""

import asyncio
import base64
import io
import json
import logging
import math
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

import cv2
import numpy as np
from PIL import Image
from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from ..config import settings
from ..utils.property_marker import apply_view_marker

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

SIDE_OFFSET_DEG: int = 35          # ± degrees from center heading for left/right
FOV: int = 90                       # Google Maps Street View horizontal FOV
VIEWPORT: Dict[str, int] = {"width": 1280, "height": 720}
INITIAL_WAIT_MS: int = 4500         # wait after networkidle for WebGL painting
STAB_WAIT_MS: int = 900             # fixed post-drag settle wait
STAB_TIMEOUT_MS: int = 3500         # max additional stability check window
STAB_DIFF_THRESHOLD: float = 1.5    # mean pixel diff below which frame is stable
RETRY_COUNT: int = 3
RETRY_DELAY_MS: int = 2500
IMAGE_QUALITY: int = 85

_STREET_VIEW_URL = (
    "https://www.google.com/maps"
    "?q={lat},{lng}"
    "&layer=c"
    "&cbll={lat},{lng}"
    "&cbp=12,{heading},,0,1"
)

_UI_SELECTORS_TO_HIDE = [
    "#gb", ".app-viewcard-strip", ".scene-footer",
    ".searchbox", "#searchboxinput",
    ".minimap", ".navigation-control", ".zoom-control",
    "button[aria-label='Street View']", "button[aria-label='Satellite']",
    "[jsaction*='settings']", ".watermark",
]

_VIEW_NAMES: List[str] = ["left", "center", "right"]


def _compute_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    if lat1 == lat2 and lon1 == lon2:
        return float("nan")
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    Δλ = math.radians(lon2 - lon1)
    x = math.sin(Δλ) * math.cos(φ2)
    y = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(Δλ)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


class StreetCaptureService:
    """
    Captures three Street View frames via Playwright + overlays property marker.
    Must be used as an async context manager (manages single browser lifetime).
    """

    def __init__(self):
        self.output_dir = (Path(settings.image_output_dir) / "street").resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._pw: Optional[Playwright] = None
        self._browser = None
        self._context: Optional[BrowserContext] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=settings.playwright_headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                f"--window-size={VIEWPORT['width']},{VIEWPORT['height']}",
            ],
        )
        self._context = await self._browser.new_context(
            viewport=VIEWPORT,
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        logger.info(f"Browser started headless={settings.playwright_headless} viewport={VIEWPORT['width']}×{VIEWPORT['height']}")

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        logger.info("Browser stopped")

    async def __aenter__(self) -> "StreetCaptureService":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ── OSM centroid lookup ────────────────────────────────────────────────────

    def _get_osm_centroid(self, lat: float, lon: float) -> Optional[Tuple[float, float]]:
        """
        Returns the (lat, lon) centroid of the closest OSM building within 50m.
        Used to compute an accurate bearing from the street camera to the building.
        """
        import httpx as _httpx
        query = (
            f"[out:json][timeout:15];"
            f"(way[\"building\"](around:50,{lat},{lon}););"
            f"out body;>;out skel qt;"
        )
        endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
        ]
        for endpoint in endpoints:
            try:
                resp = _httpx.post(endpoint, data={"data": query},
                                   headers={"User-Agent": "property-intelligence/1.0"}, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                nodes = {n["id"]: (n["lat"], n["lon"]) for n in data.get("elements", []) if n["type"] == "node"}
                ways  = [e for e in data.get("elements", []) if e["type"] == "way"]
                if not ways:
                    return None
                def dist(way):
                    pts = [nodes[nid] for nid in way["nodes"] if nid in nodes]
                    if not pts:
                        return float("inf")
                    return math.hypot(sum(p[0] for p in pts)/len(pts) - lat,
                                      sum(p[1] for p in pts)/len(pts) - lon)
                best = min(ways, key=dist)
                pts = [nodes[nid] for nid in best["nodes"] if nid in nodes]
                if not pts:
                    return None
                c_lat = sum(p[0] for p in pts) / len(pts)
                c_lon = sum(p[1] for p in pts) / len(pts)
                logger.info(f"OSM building centroid: ({c_lat:.6f},{c_lon:.6f})")
                return c_lat, c_lon
            except Exception as e:
                logger.debug(f"OSM centroid lookup failed ({endpoint}): {e}")
        return None

    # ── Street View metadata pre-check ────────────────────────────────────────

    async def _check_street_view_available(self, lat: float, lng: float) -> bool:
        """
        Call the Street View Static Metadata API before launching Playwright.
        Returns False (skip capture) when:
          - Google has no imagery at this location (status != OK)
          - The nearest pano is more than 50 m from the requested coordinates
        This prevents passing a blank/distant road photo to the vision model.
        """
        if not settings.google_maps_api_key:
            return True  # can't check without a key — proceed and let Playwright handle it
        url = "https://maps.googleapis.com/maps/api/streetview/metadata"
        params = {
            "location": f"{lat},{lng}",
            "key": settings.google_maps_api_key,
            "radius": "50",  # only accept imagery within 50 m
        }
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url, params=params)
                data = resp.json()

            status = data.get("status", "")
            if status == "REQUEST_DENIED":
                # Metadata API key not authorized — Playwright uses full Google Maps (not Static API),
                # so the key restriction doesn't block it. Proceed and let Playwright handle the capture.
                logger.warning(
                    "Street View Metadata API key not authorized (REQUEST_DENIED) — proceeding with Playwright anyway"
                )
                return True
            if status in ("ZERO_RESULTS", "NOT_FOUND", "UNKNOWN_ERROR"):
                logger.warning(
                    "Street View metadata: no coverage within 50 m of (%.5f, %.5f) — status=%s, skipping",
                    lat, lng, status,
                )
                return False
            if status != "OK":
                logger.warning(
                    "Street View metadata unexpected status=%s for (%.5f, %.5f) — proceeding with Playwright",
                    status, lat, lng,
                )
                return True

            # Verify the returned pano is actually close to the requested point
            loc = data.get("location", {})
            pano_lat = float(loc.get("lat", lat))
            pano_lng = float(loc.get("lng", lng))
            dist_m = math.hypot((pano_lat - lat) * 111_320, (pano_lng - lng) * 111_320 * math.cos(math.radians(lat)))
            if dist_m > 50:
                logger.warning(
                    "Street View pano is %.0f m away from requested coords — skipping (too far)", dist_m
                )
                return False

            logger.info("Street View metadata OK — pano %.0f m away", dist_m)
            return True

        except Exception as e:
            logger.debug("Street View metadata check failed: %s — proceeding anyway", e)
            return True  # fail open: let Playwright decide

    # ── Public API ─────────────────────────────────────────────────────────────

    async def capture_all(
        self,
        lat: float,
        lon: float,
        job_id: str,
        marker_lat: Optional[float] = None,
        marker_lon: Optional[float] = None,
    ) -> Dict[str, Optional[str]]:
        """
        Capture left, center, right Street View frames.
        Returns {"center": path, "left": path, "right": path}.
        """
        # Determine the best bearing from street camera → property building.
        # Priority: 1) provided marker coords, 2) OSM centroid, 3) fallback 0°
        tgt_lat, tgt_lon = marker_lat, marker_lon

        if tgt_lat is None or (tgt_lat == lat and tgt_lon == lon):
            # GIS estimated centroid == input point — not useful. Query OSM instead.
            osm = await asyncio.to_thread(self._get_osm_centroid, lat, lon)
            if osm:
                tgt_lat, tgt_lon = osm

        if tgt_lat is not None and not (tgt_lat == lat and tgt_lon == lon):
            bearing = _compute_bearing(lat, lon, tgt_lat, tgt_lon)
            center_heading = bearing if not math.isnan(bearing) else 0.0
        else:
            center_heading = 0.0
            logger.info("No OSM centroid offset — using default heading 0°")

        logger.info(f"Street capture lat={lat} lon={lon} center_heading={center_heading:.1f}°")

        # Pre-flight metadata check — skip Playwright entirely if no imagery within 50 m
        if not await self._check_street_view_available(lat, lon):
            logger.info("Street View skipped — no imagery within 50 m of (%.5f, %.5f)", lat, lon)
            return {"center": None, "left": None, "right": None}

        async with self:
            views = await self._capture_three_views(lat, lon, center_heading, job_id)

        result: Dict[str, Optional[str]] = {"center": None, "left": None, "right": None}
        for name, info in views.items():
            raw: Optional[Path] = info.get("raw_path")
            if raw and raw.exists():
                view_heading = info["heading"]
                img_bgr = cv2.imread(str(raw))
                if img_bgr is not None:
                    marked, _ = apply_view_marker(
                        img_bgr,
                        center_heading=center_heading,
                        view_heading=view_heading,
                        fov=float(FOV),
                        label="TARGET",
                    )
                    dest = self.output_dir / f"{job_id}_sv_{name}.jpg"
                    cv2.imwrite(str(dest), marked, [cv2.IMWRITE_JPEG_QUALITY, IMAGE_QUALITY])
                    result[name] = str(dest.resolve())
                    logger.info(f"Street view saved: {dest.name}")

        return result

    # ── Page helpers ───────────────────────────────────────────────────────────

    async def _new_page(self) -> Page:
        assert self._context, "Call start() first."
        return await self._context.new_page()

    async def _dismiss_consent(self, page: Page) -> None:
        for sel in [
            'button[aria-label="Accept all"]',
            'button:has-text("Accept all")',
            'button:has-text("Agree")',
            'form[action*="consent"] button[type="submit"]',
        ]:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2_500)
                await btn.click()
                await page.wait_for_timeout(1_200)
                logger.info(f"Consent dismissed: {sel}")
                return
            except Exception:
                continue

    async def _hide_ui_chrome(self, page: Page) -> None:
        try:
            sels_js = json.dumps(_UI_SELECTORS_TO_HIDE)
            await page.evaluate(
                f"""
                const sels = {sels_js};
                sels.forEach(s => {{
                    document.querySelectorAll(s).forEach(el => {{
                        el.style.setProperty('display', 'none', 'important');
                    }});
                }});
                """
            )
        except Exception as e:
            logger.debug(f"UI hide skipped: {e}")

    async def _dismiss_side_panel(self, page: Page) -> None:
        """Hide the place-info side panel via CSS without interacting with it (interaction can exit panorama)."""
        try:
            await page.evaluate("""
                () => {
                    // Target the left-edge info panel by data attributes and known class fragments
                    const patterns = [
                        '[data-section-id]', '[data-feature-id]',
                        '.app-viewcard-strip', '.place-page',
                    ];
                    patterns.forEach(sel => {
                        try {
                            document.querySelectorAll(sel).forEach(el => {
                                const r = el.getBoundingClientRect();
                                if (r.width > 80 && r.width < 600) {
                                    el.style.setProperty('display', 'none', 'important');
                                }
                            });
                        } catch(e) {}
                    });
                }
            """)
            logger.debug("Side panel CSS hide applied")
        except Exception as e:
            logger.debug(f"Side panel dismiss skipped: {e}")

    async def _wait_for_street_view(self, page: Page) -> None:
        try:
            await page.wait_for_selector("canvas", timeout=25_000)
            logger.info("Street View canvas detected")
        except PlaywrightTimeoutError:
            logger.warning("Street View canvas not found — proceeding anyway")

        await self._dismiss_consent(page)

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeoutError:
            logger.warning("Network idle timeout — proceeding anyway")

        await page.wait_for_timeout(INITIAL_WAIT_MS)
        await self._dismiss_side_panel(page)
        await self._hide_ui_chrome(page)

    async def _has_street_view_coverage(self, page: Page) -> bool:
        """Returns True if real Street View photography is loaded (not a black no-coverage screen)."""
        try:
            png = await page.screenshot(full_page=False, type="png")
            arr = np.array(Image.open(BytesIO(png)).convert("RGB"))
            h, w = arr.shape[:2]
            center = arr[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4]
            mean_brightness = float(np.mean(center))
            has_coverage = mean_brightness > 15.0
            if not has_coverage:
                logger.warning(f"No Street View coverage detected (dark screen, brightness={mean_brightness:.1f})")
            return has_coverage
        except Exception as e:
            logger.debug(f"Coverage brightness check failed: {e}")
            return True  # assume coverage if check fails

    async def _find_nearby_coverage(
        self, page: Page, lat: float, lon: float, heading: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """Try ±100m and ±200m offsets in 4 cardinal directions to find nearest road with Street View."""
        OFFSETS = [
            (0.0009, 0.0),   # ~100m N
            (-0.0009, 0.0),  # ~100m S
            (0.0, 0.0009),   # ~100m E
            (0.0, -0.0009),  # ~100m W
            (0.0018, 0.0),   # ~200m N
            (-0.0018, 0.0),  # ~200m S
            (0.0, 0.0018),   # ~200m E
            (0.0, -0.0018),  # ~200m W
        ]
        for dlat, dlon in OFFSETS:
            nlat, nlon = lat + dlat, lon + dlon
            url = _STREET_VIEW_URL.format(lat=nlat, lng=nlon, heading=int(heading % 360))
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                await self._wait_for_street_view(page)
                if await self._has_street_view_coverage(page):
                    logger.info(f"Fallback Street View found at offset ({dlat:+.4f},{dlon:+.4f})")
                    return nlat, nlon
            except Exception as e:
                logger.debug(f"Fallback offset ({dlat:+.4f},{dlon:+.4f}) failed: {e}")
        return None, None

    async def _get_canvas_clip(self, page: Page) -> Optional[Dict[str, int]]:
        try:
            clip = await page.evaluate("""
                () => {
                    const canvases = [...document.querySelectorAll('canvas')];
                    let best = null, bestArea = 0;
                    for (const c of canvases) {
                        const r = c.getBoundingClientRect();
                        const area = r.width * r.height;
                        if (area > bestArea && r.width > 400) {
                            bestArea = area;
                            best = {
                                x:      Math.round(r.left),
                                y:      Math.round(r.top),
                                width:  Math.round(r.width),
                                height: Math.round(r.height),
                            };
                        }
                    }
                    return best;
                }
            """)
            return clip or None
        except Exception:
            return None

    async def _wait_stable_render(self, page: Page) -> None:
        await page.wait_for_timeout(STAB_WAIT_MS)
        interval_ms = 300
        max_checks = max(1, STAB_TIMEOUT_MS // interval_ms)
        prev_small: Optional[np.ndarray] = None

        for _ in range(max_checks):
            png = await page.screenshot(full_page=False, type="png")
            arr = np.array(Image.open(BytesIO(png)).convert("RGB"))
            h_s = max(1, arr.shape[0] // 7)
            w_s = max(1, arr.shape[1] // 7)
            small = cv2.resize(arr, (w_s, h_s))
            cy_, cx_ = h_s // 5, w_s // 5
            centre = small[cy_: h_s - cy_, cx_: w_s - cx_]

            if prev_small is not None:
                diff = float(np.mean(np.abs(centre.astype(np.float32) - prev_small.astype(np.float32))))
                if diff < STAB_DIFF_THRESHOLD:
                    logger.debug(f"Render stable diff={diff:.2f}")
                    return
            prev_small = centre
            await page.wait_for_timeout(interval_ms)

        logger.warning("Render stabilisation timeout — proceeding anyway")

    async def _rotate_viewport(
        self,
        page: Page,
        drag_px: int,
        canvas_rect: Optional[Dict[str, int]] = None,
    ) -> None:
        """Rotate Street View rightward by dragging left by drag_px pixels."""
        _STEPS = 40
        _STEP_MS = 12

        cx = canvas_rect["x"]      if canvas_rect else 0
        cy = canvas_rect["y"]      if canvas_rect else 0
        cw = canvas_rect["width"]  if canvas_rect else VIEWPORT["width"]
        ch = canvas_rect["height"] if canvas_rect else VIEWPORT["height"]

        mid_y   = cy + ch // 2
        start_x = cx + int(cw * 0.75)
        actual  = min(drag_px, start_x - (cx + 20))

        await page.mouse.click(start_x, mid_y)
        await page.wait_for_timeout(150)
        await page.mouse.move(start_x, mid_y)
        await page.mouse.down()
        for step in range(1, _STEPS + 1):
            xi = start_x - int(actual * step / _STEPS)
            await page.mouse.move(xi, mid_y)
            await page.wait_for_timeout(_STEP_MS)
        await page.mouse.up()
        await page.wait_for_timeout(800)

    # ── Three-view capture ─────────────────────────────────────────────────────

    async def _capture_three_views(
        self,
        lat: float,
        lon: float,
        center_heading: float,
        job_id: str,
    ) -> Dict[str, Any]:
        left_h  = center_heading - SIDE_OFFSET_DEG
        right_h = center_heading + SIDE_OFFSET_DEG

        views: Dict[str, Any] = {
            "left":   {"heading": left_h,        "raw_path": None, "status": "failed"},
            "center": {"heading": center_heading, "raw_path": None, "status": "failed"},
            "right":  {"heading": right_h,        "raw_path": None, "status": "failed"},
        }

        url = _STREET_VIEW_URL.format(lat=lat, lng=lon, heading=int(left_h % 360))
        page = await self._new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await self._wait_for_street_view(page)

            if not await self._has_street_view_coverage(page):
                found_lat, found_lon = await self._find_nearby_coverage(page, lat, lon, left_h)
                if found_lat is None:
                    logger.warning(f"No Street View coverage within ~200m of ({lat},{lon}) — skipping")
                    return views  # finally block will close the page
                lat, lon = found_lat, found_lon

            canvas_rect = await self._get_canvas_clip(page)
            if canvas_rect:
                logger.info(f"Canvas {canvas_rect['width']}×{canvas_rect['height']} @ ({canvas_rect['x']},{canvas_rect['y']})")

            canvas_width = canvas_rect["width"] if canvas_rect else VIEWPORT["width"]
            drag_px = max(1, int(round(canvas_width * SIDE_OFFSET_DEG / FOV)))
            logger.info(f"3-view capture center={center_heading:.0f}° offset=±{SIDE_OFFSET_DEG}° drag={drag_px}px")

            for view_name in _VIEW_NAMES:
                if view_name != "left":
                    await self._rotate_viewport(page, drag_px, canvas_rect)
                    await self._wait_stable_render(page)

                for attempt in range(1, RETRY_COUNT + 1):
                    try:
                        await self._hide_ui_chrome(page)
                        raw_dest = self.output_dir / f"{job_id}_sv_{view_name}_raw.png"
                        shot_kwargs: Dict[str, Any] = {"full_page": False, "type": "png"}
                        if canvas_rect:
                            shot_kwargs["clip"] = canvas_rect

                        png = await page.screenshot(**shot_kwargs)
                        img = Image.open(BytesIO(png)).convert("RGB")
                        img.save(str(raw_dest), format="PNG")
                        views[view_name]["raw_path"] = raw_dest
                        views[view_name]["status"] = "ok"
                        logger.info(f"Captured {view_name} {img.width}×{img.height} attempt={attempt}")
                        break
                    except Exception as e:
                        logger.warning(f"View capture failed {view_name} attempt={attempt}: {e}")
                        if attempt < RETRY_COUNT:
                            await page.wait_for_timeout(RETRY_DELAY_MS)

        except Exception as e:
            logger.error(f"Three-view page error: {e}")
        finally:
            await page.close()

        ok = sum(1 for v in views.values() if v["status"] == "ok")
        logger.info(f"Three-view complete ok={ok}/3")
        return views

    def image_to_base64(self, path: str) -> str:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
