import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, Optional, List

from ..models.property import (
    PropertyJob,
    PropertyJobStatus,
    PipelineStage,
    PipelineStageStatus,
    BatchJobStatus,
    PropertyRequest,
)
from ..services.gis_service import GISService
from ..services.satellite_service import SatelliteService
from ..services.street_capture_service import StreetCaptureService
from ..services.vision_service import VisionService
from ..services.rule_engine import RuleEngine
from ..services.report_generator import ReportGenerator

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Orchestrates the full property analysis pipeline:
    GIS → Satellite → Street View → Vision → Rule Engine → Report
    """

    def __init__(self):
        self.gis = GISService()
        self.satellite = SatelliteService()
        self.vision = VisionService()
        self.rule_engine = RuleEngine()
        self.reporter = ReportGenerator()

        self._jobs: Dict[str, PropertyJob] = {}
        self._batches: Dict[str, List[str]] = {}
        self._semaphore = asyncio.Semaphore(5)

    # ── Public API ──────────────────────────────────────────────────────────

    async def submit_property(self, req: PropertyRequest) -> PropertyJob:
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        job = PropertyJob(
            job_id=job_id,
            property_id=req.property_id,
            latitude=req.latitude,
            longitude=req.longitude,
            status=PropertyJobStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = job
        asyncio.create_task(self._run(job_id))
        return job

    async def submit_batch(self, requests: List[PropertyRequest], batch_id: Optional[str] = None) -> str:
        batch_id = batch_id or str(uuid.uuid4())
        job_ids = []
        for req in requests:
            job = await self.submit_property(req)
            job_ids.append(job.job_id)
        self._batches[batch_id] = job_ids
        return batch_id

    def get_job(self, job_id: str) -> Optional[PropertyJob]:
        return self._jobs.get(job_id)

    def get_batch_status(self, batch_id: str) -> Optional[BatchJobStatus]:
        job_ids = self._batches.get(batch_id)
        if job_ids is None:
            return None

        jobs = [self._jobs[jid] for jid in job_ids if jid in self._jobs]
        counts = {s: 0 for s in ["queued", "processing", "completed", "failed"]}
        for job in jobs:
            counts[job.status.value] = counts.get(job.status.value, 0) + 1

        return BatchJobStatus(
            batch_id=batch_id,
            total=len(jobs),
            completed=counts["completed"],
            failed=counts["failed"],
            queued=counts["queued"],
            processing=counts["processing"],
            jobs=jobs,
        )

    def list_jobs(self, limit: int = 50) -> List[PropertyJob]:
        jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    # ── Internal Pipeline ───────────────────────────────────────────────────

    async def _run(self, job_id: str):
        async with self._semaphore:
            job = self._jobs[job_id]
            self._update_job(job, status=PropertyJobStatus.PROCESSING)

            try:
                # Stage 1: GIS
                parcel = await self._run_stage(job, "gis_fetch", self._stage_gis, job)

                # Stage 2: Satellite capture
                satellite_path = await self._run_stage(job, "satellite_capture", self._stage_satellite, job, parcel)

                # Stage 3: Street view capture
                street_paths = await self._run_stage(job, "street_capture", self._stage_street, job, parcel)

                # Stage 4: Vision analysis
                vision_result = await self._run_stage(job, "vision_analysis", self._stage_vision, satellite_path, street_paths)

                # Stage 5: Rule engine
                result = await self._run_stage(job, "rule_engine", self._stage_rules, vision_result, parcel)

                job.result = result
                self._update_job(job, status=PropertyJobStatus.COMPLETED)

                # Stage 6: Report generation (non-blocking)
                await self._run_stage(job, "report_generation", self._stage_report, job)

            except Exception as e:
                logger.exception(f"Pipeline failed for job {job_id}: {e}")
                self._update_job(job, status=PropertyJobStatus.FAILED, error=str(e))

    async def _run_stage(self, job: PropertyJob, name: str, fn, *args):
        stage = PipelineStage(name=name, status=PipelineStageStatus.RUNNING)
        job.stages.append(stage)
        self._update_job(job)
        t0 = time.monotonic()
        try:
            result = await fn(*args)
            stage.status = PipelineStageStatus.COMPLETED
            stage.duration_ms = (time.monotonic() - t0) * 1000
            return result
        except Exception as e:
            stage.status = PipelineStageStatus.FAILED
            stage.error = str(e)
            stage.duration_ms = (time.monotonic() - t0) * 1000
            logger.error(f"Stage {name} failed: {e}")
            raise

    async def _stage_gis(self, job: PropertyJob):
        return await self.gis.get_parcel(job.latitude, job.longitude)

    async def _stage_satellite(self, job: PropertyJob, parcel):
        return await self.satellite.capture(job.latitude, job.longitude, parcel or {}, job.job_id)

    async def _stage_street(self, job: PropertyJob, parcel):
        # Use OSM parcel centroid as marker target for accurate bearing calculation
        centroid = (parcel or {}).get("centroid")
        marker_lat = centroid[1] if centroid else None
        marker_lon = centroid[0] if centroid else None
        svc = StreetCaptureService()
        return await svc.capture_all(
            job.latitude, job.longitude, job.job_id,
            marker_lat=marker_lat, marker_lon=marker_lon,
        )

    async def _stage_vision(self, satellite_path, street_paths):
        return await self.vision.analyze(satellite_path, street_paths or {})

    async def _stage_rules(self, vision_result, parcel):
        return self.rule_engine.evaluate(vision_result, parcel)

    async def _stage_report(self, job: PropertyJob):
        return self.reporter.generate(job)

    def _update_job(self, job: PropertyJob, **kwargs):
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow().isoformat()
