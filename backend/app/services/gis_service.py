import httpx
import asyncio
from typing import Optional, Dict, Any, List, Tuple
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import transform
import pyproj
import json
import logging

from ..config import settings

logger = logging.getLogger(__name__)


class GISService:
    """Fetches parcel boundary data from county GIS / ArcGIS APIs."""

    REGRID_API = "https://app.regrid.com/api/v1/parcel"
    ARCGIS_SAMPLE = "https://services.arcgis.com"

    async def get_parcel(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """
        Attempts parcel lookup in order:
        1. Regrid (nationwide coverage)
        2. FCC Area API fallback for basic boundary estimation
        """
        parcel = await self._fetch_regrid(lat, lon)
        if parcel:
            return parcel

        logger.warning(f"Regrid failed for ({lat},{lon}), using estimated boundary")
        return self._create_estimated_parcel(lat, lon)

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

    def _create_estimated_parcel(self, lat: float, lon: float) -> Dict[str, Any]:
        """Creates a ~100x100ft estimated parcel centered on coords."""
        delta_lat = 0.00045
        delta_lon = 0.00055

        coords = [
            [lon - delta_lon, lat - delta_lat],
            [lon + delta_lon, lat - delta_lat],
            [lon + delta_lon, lat + delta_lat],
            [lon - delta_lon, lat + delta_lat],
            [lon - delta_lon, lat - delta_lat],
        ]
        geom = {"type": "Polygon", "coordinates": [coords]}
        polygon = Polygon([(c[0], c[1]) for c in coords[:-1]])

        return {
            "geometry": geom,
            "polygon": polygon,
            "bbox": list(polygon.bounds),
            "centroid": [lon, lat],
            "area_sqft": self._calc_area_sqft(polygon),
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
