from fastapi import APIRouter
from ..config import settings
from ..services.vision_service import VisionService, TEST_MODEL

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    vision = VisionService()
    ollama_ok = await vision.check_ollama_health()
    return {
        "status": "ok",
        "version": settings.app_version,
        "ollama": "connected" if ollama_ok else "disconnected",
        "model": TEST_MODEL,  # switch to settings.ollama_model when using MiniCPM-V
    }
