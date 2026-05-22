import logging
from collections import defaultdict
from typing import DefaultDict, Dict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        self._connections: DefaultDict[str, Set[WebSocket]] = defaultdict(set)

    async def connect(self, stream_id: str, websocket: WebSocket) -> int:
        await websocket.accept()
        self._connections[stream_id].add(websocket)
        count = self.viewer_count(stream_id)
        logger.info("WebSocket connected stream=%s viewers=%s", stream_id, count)
        return count

    def disconnect(self, stream_id: str, websocket: WebSocket) -> int:
        self._connections[stream_id].discard(websocket)
        if not self._connections[stream_id]:
            self._connections.pop(stream_id, None)
        count = self.viewer_count(stream_id)
        logger.info("WebSocket disconnected stream=%s viewers=%s", stream_id, count)
        return count

    def viewer_count(self, stream_id: str) -> int:
        return len(self._connections.get(stream_id, set()))

    async def broadcast(self, stream_id: str, message: Dict) -> None:
        disconnected = []
        for websocket in list(self._connections.get(stream_id, set())):
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(stream_id, websocket)
