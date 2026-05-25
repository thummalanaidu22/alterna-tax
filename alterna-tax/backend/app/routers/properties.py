from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from ..models.property import PropertyRequest, PropertyJob, BatchPropertyRequest, BatchJobStatus
from ..pipeline.orchestrator import PipelineOrchestrator
from ..auth import require_api_key

_MAX_BATCH_SIZE = 500

router = APIRouter(
    prefix="/api/properties",
    tags=["properties"],
    dependencies=[Depends(require_api_key)],
)

_orchestrator: Optional[PipelineOrchestrator] = None


def get_orchestrator() -> PipelineOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PipelineOrchestrator()
    return _orchestrator


class ReviewRequest(BaseModel):
    verdict: str        # "approved" | "rejected"
    notes: str = ""


@router.post("/analyze", response_model=PropertyJob, status_code=202)
async def analyze_property(req: PropertyRequest):
    """Submit a single property for analysis. Returns job object immediately."""
    return await get_orchestrator().submit_property(req)


@router.post("/batch", response_model=dict, status_code=202)
async def analyze_batch(req: BatchPropertyRequest):
    """Submit a batch of properties (max 500). Returns batch_id immediately."""
    if len(req.properties) > _MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Batch size {len(req.properties)} exceeds the maximum of {_MAX_BATCH_SIZE}. "
                "Split into smaller batches."
            ),
        )
    batch_id = await get_orchestrator().submit_batch(req.properties, req.batch_id)
    return {"batch_id": batch_id, "total": len(req.properties)}


@router.get("/jobs", response_model=List[PropertyJob])
async def list_jobs(limit: int = 100):
    """List recent property analysis jobs (includes jobs from previous server sessions)."""
    return get_orchestrator().list_jobs(limit)


@router.get("/jobs/{job_id}", response_model=PropertyJob)
async def get_job(job_id: str):
    """Get status and result of a specific job."""
    job = get_orchestrator().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.put("/jobs/{job_id}/review", response_model=dict)
async def review_job(job_id: str, req: ReviewRequest):
    """Submit a human verdict for a NEEDS_HUMAN_REVIEW job."""
    allowed = {"approved", "rejected"}
    if req.verdict.lower() not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"verdict must be one of: {', '.join(allowed)}",
        )
    updated = await get_orchestrator().update_job_review(
        job_id, req.verdict.lower(), req.notes
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return {"job_id": job_id, "verdict": req.verdict.lower(), "status": "recorded"}


@router.get("/review-queue", response_model=List[PropertyJob])
async def get_review_queue(limit: int = 200):
    """List jobs awaiting human review (decision=NEEDS_HUMAN_REVIEW, no verdict yet)."""
    return await get_orchestrator().get_review_queue(limit)


@router.get("/batch/{batch_id}", response_model=BatchJobStatus)
async def get_batch_status(batch_id: str):
    """Get aggregated status of a batch job."""
    status = get_orchestrator().get_batch_status(batch_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    return status
