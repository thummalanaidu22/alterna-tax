import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from ..models.property import (
    BatchJobStatus,
    PipelineStage,
    PipelineStageStatus,
    PropertyJob,
    PropertyJobStatus,
    PropertyRequest,
)
from ..services.gis_service import GISService
from ..services.satellite_service import SatelliteService
from ..services.street_capture_service import StreetCaptureService
from ..services.vision_service import VisionService
from ..services.rule_engine import RuleEngine
from ..services.report_generator import ReportGenerator
from .. import db
from ..ws_manager import ws_manager

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Orchestrates the full property analysis pipeline:
    GIS → Satellite → Street View → Vision → Rule Engine → Report
    Jobs are persisted to SQLite so they survive server restarts.
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

    async def startup(self) -> None:
        """Start with a clean in-memory state on every server restart.
        New jobs are still persisted to SQLite during the session.
        """
        self._jobs.clear()
        self._batches.clear()
        logger.info("Pipeline orchestrator ready — starting with clean state")

    # ── Public API ─────────────────────────────────────────────────────────────

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
        await self._update_job(job)
        asyncio.create_task(self._run(job_id))
        return job

    async def submit_batch(
        self, requests: List[PropertyRequest], batch_id: Optional[str] = None
    ) -> str:
        batch_id = batch_id or str(uuid.uuid4())
        seen: set = set()
        job_ids: List[str] = []
        for req in requests:
            key = (round(req.latitude, 6), round(req.longitude, 6))
            if key in seen:
                logger.warning("Duplicate coordinates in batch, skipping: %s", key)
                continue
            seen.add(key)
            job = await self.submit_property(req)
            job_ids.append(job.job_id)
        self._batches[batch_id] = job_ids
        asyncio.create_task(db.upsert_batch(batch_id, job_ids))
        return batch_id

    def get_job(self, job_id: str) -> Optional[PropertyJob]:
        return self._jobs.get(job_id)

    def get_batch_status(self, batch_id: str) -> Optional[BatchJobStatus]:
        job_ids = self._batches.get(batch_id)
        if job_ids is None:
            return None
        jobs = [self._jobs[jid] for jid in job_ids if jid in self._jobs]
        counts: Dict[str, int] = {s: 0 for s in ["queued", "processing", "completed", "failed"]}
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

    def list_jobs(self, limit: int = 100) -> List[PropertyJob]:
        jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    async def update_job_review(
        self, job_id: str, human_verdict: str, reviewer_notes: str
    ) -> bool:
        updated = await db.update_review(job_id, human_verdict, reviewer_notes)
        if updated and job_id in self._jobs:
            self._jobs[job_id].human_verdict = human_verdict
            self._jobs[job_id].reviewer_notes = reviewer_notes
        return updated

    async def get_review_queue(self, limit: int = 200) -> List[PropertyJob]:
        rows = await db.list_review_queue_from_db(limit)
        jobs = []
        for row in rows:
            # Prefer in-memory (most up-to-date), fall back to DB row
            job = self._jobs.get(row["job_id"])
            if job is None:
                try:
                    job = PropertyJob(**row)
                except Exception:
                    continue
            if not job.human_verdict:
                jobs.append(job)
        return jobs

    # ── Internal Pipeline ──────────────────────────────────────────────────────

    async def _run(self, job_id: str) -> None:
        async with self._semaphore:
            job = self._jobs[job_id]
            await self._update_job(job, status=PropertyJobStatus.PROCESSING)

            try:
                gis_stage = PipelineStage(name="gis_fetch", status=PipelineStageStatus.RUNNING)
                sat_stage = PipelineStage(name="satellite_capture", status=PipelineStageStatus.RUNNING)
                str_stage = PipelineStage(name="street_capture", status=PipelineStageStatus.RUNNING)
                job.stages.extend([gis_stage, sat_stage, str_stage])
                await self._update_job(job)

                t0 = time.monotonic()
                parcel, satellite_path, street_paths = await asyncio.gather(
                    self._timed_stage(gis_stage, self._stage_gis(job)),
                    self._timed_stage(sat_stage, self._stage_satellite_no_parcel(job)),
                    self._timed_stage(str_stage, self._stage_street_no_parcel(job)),
                    return_exceptions=True,
                )
                logger.info("Parallel capture done in %dms", (time.monotonic() - t0) * 1000)

                if isinstance(parcel, Exception):
                    logger.warning("GIS failed: %s", parcel); parcel = None
                if isinstance(satellite_path, Exception):
                    logger.warning("Satellite failed: %s", satellite_path); satellite_path = None
                if isinstance(street_paths, Exception):
                    logger.warning("Street failed: %s", street_paths); street_paths = {}

                # Record how many street view images were actually saved
                job.street_view_count = sum(1 for v in (street_paths or {}).values() if v is not None)

                vision_result = await self._run_stage(
                    job, "vision_analysis", self._stage_vision, satellite_path, street_paths
                )

                aerial_available = satellite_path is not None
                parcel_with_flags = {**(parcel or {}), "aerial_image_available": aerial_available}
                result = await self._run_stage(
                    job, "rule_engine", self._stage_rules, vision_result, parcel_with_flags
                )

                job.result = result
                await self._update_job(job, status=PropertyJobStatus.COMPLETED)

                await self._run_stage(job, "report_generation", self._stage_report, job)

            except Exception as e:
                logger.exception("Pipeline failed for job %s: %s", job_id, e)
                await self._update_job(job, status=PropertyJobStatus.FAILED, error=str(e))

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
        await self._update_job(job)
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
            logger.error("Stage %s failed: %s", name, e)
            raise

    async def _stage_gis(self, job: PropertyJob):
        return await self.gis.get_parcel(job.latitude, job.longitude)

    async def _stage_satellite_no_parcel(self, job: PropertyJob):
        return await self.satellite.capture(job.latitude, job.longitude, {}, job.job_id)

    async def _stage_street_no_parcel(self, job: PropertyJob):
        svc = StreetCaptureService()
        return await svc.capture_all(job.latitude, job.longitude, job.job_id)

    async def _stage_vision(self, satellite_path, street_paths):
        return await self.vision.analyze(satellite_path, street_paths or {})

    async def _stage_rules(self, vision_result, parcel):
        return self.rule_engine.evaluate(vision_result, parcel)

    async def _stage_report(self, job: PropertyJob):
        return self.reporter.generate(job)

    async def _update_job(self, job: PropertyJob, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow().isoformat()

        # Persist to SQLite (fire-and-forget — don't block the pipeline)
        job_dict = job.model_dump(mode="json")
        asyncio.create_task(db.upsert_job(job_dict))

        # Push update to any connected WebSocket clients
        asyncio.create_task(ws_manager.send_job_update(job_dict))
