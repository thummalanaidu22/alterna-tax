import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import properties_router, health_router
from .ws_manager import ws_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for d in ["data/images/satellite", "data/images/street", "data/reports"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    await init_db()

    # Restore persisted jobs into the orchestrator
    from .routers.properties import get_orchestrator
    await get_orchestrator().startup()

    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-powered property due diligence platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(properties_router)

# Serve generated reports and images
for _static_dir, _mount_path in [
    ("data/images", "/images"),
    ("data/reports", "/reports"),
]:
    _p = Path(_static_dir)
    _p.mkdir(parents=True, exist_ok=True)
    app.mount(
        _mount_path,
        StaticFiles(directory=str(_p)),
        name=_static_dir.replace("/", "_"),
    )


@app.get("/")
async def root():
    return {
        "message": settings.app_name,
        "version": settings.app_version,
        "model": settings.ollama_model,
        "docs": "/docs",
    }


@app.websocket("/api/ws/jobs/{job_id}")
async def job_websocket(job_id: str, websocket: WebSocket):
    """Real-time job status updates — connect once, receive pushes on every pipeline stage."""
    await ws_manager.connect(job_id, websocket)
    try:
        from .routers.properties import get_orchestrator
        job = get_orchestrator().get_job(job_id)
        if job:
            await websocket.send_json(job.model_dump(mode="json"))
        # Keep connection open; updates are pushed by the orchestrator
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(job_id, websocket)
