import aiosqlite
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = "data/propintel.db"


async def init_db() -> None:
    Path("data").mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id         TEXT PRIMARY KEY,
                property_id    TEXT,
                latitude       REAL NOT NULL,
                longitude      REAL NOT NULL,
                status         TEXT NOT NULL,
                stages         TEXT DEFAULT '[]',
                result         TEXT,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                error          TEXT,
                human_verdict  TEXT,
                reviewer_notes TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS batches (
                batch_id   TEXT PRIMARY KEY,
                job_ids    TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status)")
        await db.commit()
    logger.info("SQLite database ready at %s", DB_PATH)


async def upsert_job(job: Dict[str, Any]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO jobs
                (job_id, property_id, latitude, longitude, status, stages, result,
                 created_at, updated_at, error, human_verdict, reviewer_notes)
            VALUES
                (:job_id, :property_id, :latitude, :longitude, :status, :stages, :result,
                 :created_at, :updated_at, :error, :human_verdict, :reviewer_notes)
            ON CONFLICT(job_id) DO UPDATE SET
                status         = excluded.status,
                stages         = excluded.stages,
                result         = excluded.result,
                updated_at     = excluded.updated_at,
                error          = excluded.error,
                human_verdict  = excluded.human_verdict,
                reviewer_notes = excluded.reviewer_notes
            """,
            {
                "job_id":         job["job_id"],
                "property_id":    job.get("property_id"),
                "latitude":       job["latitude"],
                "longitude":      job["longitude"],
                "status":         job["status"],
                "stages":         json.dumps(job.get("stages", [])),
                "result":         json.dumps(job["result"]) if job.get("result") else None,
                "created_at":     job["created_at"],
                "updated_at":     job["updated_at"],
                "error":          job.get("error"),
                "human_verdict":  job.get("human_verdict"),
                "reviewer_notes": job.get("reviewer_notes"),
            },
        )
        await db.commit()


async def get_job_from_db(job_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None


async def list_jobs_from_db(limit: int = 100) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]


async def list_review_queue_from_db(limit: int = 200) -> List[Dict[str, Any]]:
    """Jobs that need human review and haven't been verdicted yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM jobs
            WHERE  status = 'completed'
              AND  json_extract(result, '$.decision') = 'NEEDS_HUMAN_REVIEW'
              AND  (human_verdict IS NULL OR human_verdict = '')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]


async def update_review(job_id: str, human_verdict: str, reviewer_notes: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """UPDATE jobs
               SET human_verdict = ?, reviewer_notes = ?, updated_at = ?
               WHERE job_id = ?""",
            (human_verdict, reviewer_notes, datetime.utcnow().isoformat(), job_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def upsert_batch(batch_id: str, job_ids: List[str]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO batches (batch_id, job_ids, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(batch_id) DO UPDATE SET job_ids = excluded.job_ids""",
            (batch_id, json.dumps(job_ids), datetime.utcnow().isoformat()),
        )
        await db.commit()


async def get_batch_job_ids(batch_id: str) -> Optional[List[str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT job_ids FROM batches WHERE batch_id = ?", (batch_id,)
        ) as cur:
            row = await cur.fetchone()
            return json.loads(row[0]) if row else None


def _row_to_dict(row: aiosqlite.Row) -> Dict[str, Any]:
    d = dict(row)
    d["stages"] = json.loads(d.get("stages") or "[]")
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    return d
