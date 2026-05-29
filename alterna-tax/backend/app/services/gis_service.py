import httpx
from typing import Optional, Dict, Any, List
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import transform
import pyproj
import logging

from ..config import settings

logger = logging.getLogger(__name__)


class GISService:
    """Fetches parcel boundary data from county GIS / ArcGIS APIs."""

    REGRID_API = "https://app.regrid.com/api/v1/parcel"
    ARCGIS_SAMPLE = "https://services.arcgis.com"

    async def get_parcel(self, lat: float, lon: float, property_type_hint: str = "") -> Optional[Dict[str, Any]]:
        """
        Attempts parcel lookup in order:
        1. Regrid (nationwide coverage, requires paid API key)
        2. OSM landuse/plot polygon — free, no API key, surprisingly accurate
        3. Auto-detect property type from OSM context, then use type-aware estimate
        """
        parcel = await self._fetch_regrid(lat, lon)
        if parcel:
            logger.info("Parcel boundary from Regrid API")
            return parcel

        parcel = await self._fetch_osm_plot(lat, lon)
        if parcel:
            logger.info("Parcel boundary from OSM landuse query")
            return parcel

        # No real boundary available — detect property type from OSM context so the
        # estimated box is the right size for this specific property
        detected_type = (property_type_hint or "").lower()
        if not detected_type:
            detected_type = await self._detect_type_from_osm(lat, lon)
            if detected_type:
                logger.info(f"OSM context detected property type: {detected_type}")

        logger.warning(f"All parcel sources failed for ({lat},{lon}) — using {detected_type or 'default'} estimate")
        return self._create_estimated_parcel(lat, lon, detected_type)

    async def _fetch_regrid(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        try:
            params = {
                "lat": lat,
                "lon": lon,
                "token": settings.arcgis_api_key or "",
                "return_geometry": True,
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(self.REGRID_API, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    parcels = data.get("parcels", {}).get("features", [])
                    if parcels:
                        return self._normalize_parcel(parcels[0])
        except Exception as e:
            logger.debug(f"Regrid error: {e}")
        return None

    def _normalize_parcel(self, feature: Dict[str, Any]) -> Dict[str, Any]:
        geom = feature.get("geometry", {})
        props = feature.get("properties", {})
        polygon = shape(geom) if geom else None

        return {
            "geometry": geom,
            "polygon": polygon,
            "bbox": list(polygon.bounds) if polygon else None,
            "centroid": [polygon.centroid.x, polygon.centroid.y] if polygon else None,
            "area_sqft": self._calc_area_sqft(polygon),
            "properties": props,
        }

    async def _detect_type_from_osm(self, lat: float, lon: float) -> str:
        """
        Query OSM tags within 150m to auto-detect property type when no hint is given.
        Uses simple around: radius queries — avoids 406 errors from complex is_in/pivot syntax.
        Returns one of: 'residential', 'commercial', 'agriculture', 'vacant', or ''
        """
        query = (
            f"[out:json][timeout:10];"
            f"(way[\"landuse\"](around:200,{lat},{lon});"
            f"way[\"building\"](around:100,{lat},{lon});"
            f"way[\"amenity\"](around:100,{lat},{lon});"
            f"way[\"shop\"](around:100,{lat},{lon}););"
            f"out tags;"
        )
        overpass_endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
        ]

        _COMMERCIAL_LANDUSE = {"commercial", "retail", "industrial", "office", "warehouse"}
        _AGRI_LANDUSE = {"farmland", "farmyard", "farm", "orchard", "vineyard",
                         "meadow", "grass", "pasture", "forest", "wood"}
        _RESI_LANDUSE = {"residential", "housing"}

        for endpoint in overpass_endpoints:
            try:
                async with httpx.AsyncClient(timeout=12) as client:
                    resp = await client.post(endpoint, data={"data": query})
                    resp.raise_for_status()
                    elements = resp.json().get("elements", [])

                scores = {"commercial": 0, "agriculture": 0, "residential": 0, "vacant": 0}
                for el in elements:
                    tags = el.get("tags", {})
                    landuse = tags.get("landuse", "").lower()
                    building = tags.get("building", "").lower()
                    amenity  = tags.get("amenity", "").lower()
                    shop     = tags.get("shop", "").lower()
                    natural  = tags.get("natural", "").lower()

                    if landuse in _COMMERCIAL_LANDUSE or amenity or shop:
                        scores["commercial"] += 2
                    elif landuse in _AGRI_LANDUSE or natural in ("wood", "scrub", "grassland", "heath"):
                        scores["agriculture"] += 2
                    elif landuse in _RESI_LANDUSE or building in ("house", "residential", "detached", "apartments"):
                        scores["residential"] += 2
                    elif building:
                        scores["commercial"] += 1  # unknown building → assume commercial

                if not any(scores.values()):
                    # Nothing found nearby — likely rural vacant/agriculture
                    return "agriculture"

                best = max(scores, key=lambda k: scores[k])
                if scores[best] == 0:
                    return ""
                logger.debug(f"OSM type scores: {scores} → {best}")
                return best

            except Exception as e:
                logger.debug(f"OSM type detection failed ({endpoint}): {e}")
                continue
        return ""

    async def _fetch_osm_plot(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """
        Query OSM Overpass for a landuse/building polygon near this point.
        Uses simple around: radius — avoids 406 errors caused by is_in/pivot syntax.
        Returns the smallest matching polygon (most specific to this property).
        Free — no API key required.
        """
        query = (
            f"[out:json][timeout:12];"
            f"(way[\"landuse\"](around:50,{lat},{lon});"
            f"way[\"building\"](around:30,{lat},{lon}););"
            f"out body;>;out skel qt;"
        )
        overpass_endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
        ]
        for endpoint in overpass_endpoints:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(endpoint, data={"data": query})
                    resp.raise_for_status()
                    data = resp.json()

                nodes = {n["id"]: (n["lon"], n["lat"])
                         for n in data.get("elements", []) if n["type"] == "node"}
                ways = [e for e in data.get("elements", []) if e["type"] == "way"]
                if not ways:
                    continue

                # Build polygons and pick the smallest one (most specific to this property)
                candidates = []
                for way in ways:
                    pts = [nodes[nid] for nid in way.get("nodes", []) if nid in nodes]
                    if len(pts) < 3:
                        continue
                    try:
                        poly = Polygon(pts)
                        if poly.is_valid and poly.area > 0:
                            candidates.append(poly)
                    except Exception:
                        continue

                if not candidates:
                    continue

                # Smallest valid polygon = most specific parcel
                best = min(candidates, key=lambda p: p.area)
                geom = mapping(best)
                area_sqft = self._calc_area_sqft(best)
                logger.info(f"OSM plot polygon: {area_sqft:,.0f} sqft via {endpoint}")
                return {
                    "geometry": geom,
                    "polygon": best,
                    "bbox": list(best.bounds),
                    "centroid": [best.centroid.x, best.centroid.y],
                    "area_sqft": area_sqft,
                    "properties": {"source": "osm_landuse"},
                }
            except Exception as e:
                logger.debug(f"OSM plot query failed ({endpoint}): {e}")
                continue
        return None

    def _create_estimated_parcel(self, lat: float, lon: float, property_type_hint: str = "") -> Dict[str, Any]:
        """
        Last-resort estimated parcel. Size varies by property type so the red boundary
        shown in the satellite image actually reflects the typical parcel size.
        """
        hint = (property_type_hint or "").lower()

        # Parcel size estimates by type (half-side in degrees)
        # lat: 1 deg ≈ 111,320m  |  lon: 1 deg ≈ 111,320 * cos(lat) m (≈ 97,000m at 28°N)
        # MINIMUM 60m side — GPS coords can be 20-40m from building, box must cover the offset
        if "agri" in hint or "farm" in hint or "ranch" in hint or "grove" in hint:
            # Agriculture: ~5 acres = ~450m × 450m
            delta_lat, delta_lon = 0.00202, 0.00232
        elif "commercial" in hint or "retail" in hint or "office" in hint or "industrial" in hint:
            # Commercial: ~300ft × 300ft
            delta_lat, delta_lon = 0.000413, 0.000464
        elif "vacant" in hint or "unimproved" in hint or "land" in hint:
            # Vacant land: ~250ft × 250ft
            delta_lat, delta_lon = 0.000344, 0.000387
        else:
            # Residential / unknown default: ~200ft × 200ft
            # Large enough to contain house even when GPS coord is at street edge
            delta_lat, delta_lon = 0.000275, 0.000309

        coords = [
            [lon - delta_lon, lat - delta_lat],
            [lon + delta_lon, lat - delta_lat],
            [lon + delta_lon, lat + delta_lat],
            [lon - delta_lon, lat + delta_lat],
            [lon - delta_lon, lat - delta_lat],
        ]
        geom = {"type": "Polygon", "coordinates": [coords]}
        polygon = Polygon([(c[0], c[1]) for c in coords[:-1]])
        area_sqft = self._calc_area_sqft(polygon)
        logger.info(f"Estimated parcel ({hint or 'residential'}): {area_sqft:,.0f} sqft")

        return {
            "geometry": geom,
            "polygon": polygon,
            "bbox": list(polygon.bounds),
            "centroid": [lon, lat],
            "area_sqft": area_sqft,
            "properties": {"estimated": True},
        }

    def _calc_area_sqft(self, polygon: Optional[Polygon]) -> Optional[float]:
        if polygon is None:
            return None
        try:
            wgs84 = pyproj.CRS("EPSG:4326")
            utm = pyproj.CRS("EPSG:32614")
            project = pyproj.Transformer.from_crs(wgs84, utm, always_xy=True).transform
            utm_poly = transform(project, polygon)
            return utm_poly.area * 10.7639  # m² → ft²
        except Exception:
            return None

    def get_parcel_overlay_coords(
        self, parcel: Dict[str, Any]
    ) -> List[List[float]]:
        """Returns [lng, lat] coordinate list for overlay rendering."""
        geom = parcel.get("geometry", {})
        if not geom:
            return []
        coords = geom.get("coordinates", [])
        if not coords:
            return []
        # Handle Polygon (take outer ring)
        ring = coords[0] if isinstance(coords[0][0], list) else coords
        return ring
