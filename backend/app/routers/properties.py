import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List, Optional

from ..models.property import PropertyRequest, PropertyJob, BatchPropertyRequest, BatchJobStatus
from ..pipeline.orchestrator import PipelineOrchestrator

router = APIRouter(prefix="/api/properties", tags=["properties"])

# Single orchestrator instance shared across requests
_orchestrator: Optional[PipelineOrchestrator] = None


def get_orchestrator() -> PipelineOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PipelineOrchestrator()
    return _orchestrator


@router.post("/analyze", response_model=PropertyJob, status_code=202)
async def analyze_property(req: PropertyRequest):
    """Submit a single property for analysis. Returns job object immediately."""
    orchestrator = get_orchestrator()
    job = await orchestrator.submit_property(req)
    return job


@router.post("/batch", response_model=dict, status_code=202)
async def analyze_batch(req: BatchPropertyRequest):
    """Submit a batch of properties. Returns batch_id."""
    orchestrator = get_orchestrator()
    batch_id = await orchestrator.submit_batch(req.properties, req.batch_id)
    return {"batch_id": batch_id, "total": len(req.properties)}


@router.get("/jobs", response_model=List[PropertyJob])
async def list_jobs(limit: int = 50):
    """List recent property analysis jobs."""
    orchestrator = get_orchestrator()
    return orchestrator.list_jobs(limit)


@router.get("/jobs/{job_id}", response_model=PropertyJob)
async def get_job(job_id: str):
    """Get status and result of a specific job."""
    orchestrator = get_orchestrator()
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.get("/batch/{batch_id}", response_model=BatchJobStatus)
async def get_batch_status(batch_id: str):
    """Get status of a batch job."""
    orchestrator = get_orchestrator()
    status = orchestrator.get_batch_status(batch_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    return status
