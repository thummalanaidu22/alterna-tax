import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import settings
from .routers import properties_router, health_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directories exist
    for d in ["data/images/satellite", "data/images/street", "data/reports"]:
        Path(d).mkdir(parents=True, exist_ok=True)
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
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
for static_dir, mount_path in [
    ("data/images", "/images"),
    ("data/reports", "/reports"),
]:
    p = Path(static_dir)
    p.mkdir(parents=True, exist_ok=True)
    app.mount(mount_path, StaticFiles(directory=str(p)), name=static_dir.replace("/", "_"))


@app.get("/")
async def root():
    return {"message": settings.app_name, "version": settings.app_version, "docs": "/docs"}
