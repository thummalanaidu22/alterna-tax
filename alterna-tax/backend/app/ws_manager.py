import logging
from typing import Dict, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSConnectionManager:
    def __init__(self):
        self._connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, job_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(job_id, []).append(ws)
        logger.debug("WS connected for job %s", job_id)

    def disconnect(self, job_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(job_id, [])
        if ws in conns:
            conns.remove(ws)

    async def send_job_update(self, job_dict: dict) -> None:
        job_id = job_dict.get("job_id")
        if not job_id:
            return
        dead: List[WebSocket] = []
        for ws in list(self._connections.get(job_id, [])):
            try:
                await ws.send_json(job_dict)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(job_id, ws)


ws_manager = WSConnectionManager()
