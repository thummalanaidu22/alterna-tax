from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # API
    app_name: str = "Property Intelligence System"
    app_version: str = "1.0.0"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Auth — set API_KEY in .env to require X-API-Key on all property endpoints.
    # Leave blank to run in open/dev mode (no key required).
    api_key: Optional[str] = None

    # Google Maps
    google_maps_api_key: Optional[str] = None

    # Mapbox
    mapbox_token: Optional[str] = None

    # ArcGIS
    arcgis_api_key: Optional[str] = None

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "minicpm-v"
    ollama_timeout: int = 120

    # Image capture
    satellite_zoom: int = 20          # zoom 20 = 0.15m/pixel — 2x sharper than zoom 19
    satellite_image_width: int = 800
    satellite_image_height: int = 800
    street_view_width: int = 1280
    street_view_height: int = 720
    street_view_headings: list[int] = [0, 90, 270]  # center, right, left

    # Playwright
    playwright_headless: bool = True
    playwright_timeout: int = 30000

    # Storage
    image_output_dir: str = "data/images"
    report_output_dir: str = "data/reports"

    # Processing
    max_concurrent_jobs: int = 5
    job_timeout_seconds: int = 300

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
