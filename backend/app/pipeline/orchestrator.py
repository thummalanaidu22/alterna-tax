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
        seen: set = set()
        job_ids = []
        for req in requests:
            key = (round(req.latitude, 6), round(req.longitude, 6))
            if key in seen:
                logger.warning(f"Duplicate coordinates in batch, skipping: {key}")
                continue
            seen.add(key)
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
                # Stages 1-3 run in PARALLEL: GIS, Satellite, Street View all need
                # only lat/lon and don't depend on each other.
                gis_stage = PipelineStage(name="gis_fetch", status=PipelineStageStatus.RUNNING)
                sat_stage = PipelineStage(name="satellite_capture", status=PipelineStageStatus.RUNNING)
                str_stage = PipelineStage(name="street_capture", status=PipelineStageStatus.RUNNING)
                job.stages.extend([gis_stage, sat_stage, str_stage])
                self._update_job(job)

                t0 = time.monotonic()
                parcel, satellite_path, street_paths = await asyncio.gather(
                    self._timed_stage(gis_stage, self._stage_gis(job)),
                    self._timed_stage(sat_stage, self._stage_satellite_no_parcel(job)),
                    self._timed_stage(str_stage, self._stage_street_no_parcel(job)),
                    return_exceptions=True,
                )
                logger.info(f"Parallel capture done in {(time.monotonic()-t0)*1000:.0f}ms")

                # Treat exceptions as None (pipeline continues with partial data)
                if isinstance(parcel, Exception):
                    logger.warning(f"GIS failed: {parcel}"); parcel = None
                if isinstance(satellite_path, Exception):
                    logger.warning(f"Satellite failed: {satellite_path}"); satellite_path = None
                if isinstance(street_paths, Exception):
                    logger.warning(f"Street failed: {street_paths}"); street_paths = {}

                # Stage 4: Vision analysis
                vision_result = await self._run_stage(job, "vision_analysis", self._stage_vision, satellite_path, street_paths)

                # Stage 5: Rule engine — pass aerial availability so SOP can enforce it
                aerial_available = satellite_path is not None
                parcel_with_flags = {**(parcel or {}), "aerial_image_available": aerial_available}
                result = await self._run_stage(job, "rule_engine", self._stage_rules, vision_result, parcel_with_flags)

                job.result = result
                self._update_job(job, status=PropertyJobStatus.COMPLETED)

                # Stage 6: Report (non-blocking)
                await self._run_stage(job, "report_generation", self._stage_report, job)

            except Exception as e:
                logger.exception(f"Pipeline failed for job {job_id}: {e}")
                self._update_job(job, status=PropertyJobStatus.FAILED, error=str(e))

    async def _timed_stage(self, stage: PipelineStage, coro):
        t0 = time.monotonic()
        try:
            result = await coro
            stage.status = PipelineStageStatus.COMPLETED
            stage.duration_ms = (time.monotonic() - t0) * 1000
            return result
        except Exception as e:
            stage.status = PipelineStageStatus.FAILED
            stage.error = str(e)
            stage.duration_ms = (time.monotonic() - t0) * 1000
            raise

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

    async def _stage_satellite_no_parcel(self, job: PropertyJob):
        """Satellite capture without waiting for GIS parcel (uses lat/lon only)."""
        return await self.satellite.capture(job.latitude, job.longitude, {}, job.job_id)

    async def _stage_street_no_parcel(self, job: PropertyJob):
        """Street capture without waiting for GIS parcel."""
        svc = StreetCaptureService()
        return await svc.capture_all(job.latitude, job.longitude, job.job_id)

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
