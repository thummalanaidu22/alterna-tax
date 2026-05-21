from fastapi import APIRouter
from ..config import settings
from ..services.vision_service import VisionService, PROD_MODEL

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    vision = VisionService()
    ollama_ok = await vision.check_ollama_health()
    return {
        "status": "ok",
        "version": settings.app_version,
        "ollama": "connected" if ollama_ok else "disconnected",
        "model": PROD_MODEL,
    }
