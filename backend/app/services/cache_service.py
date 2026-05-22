import json
import logging
from typing import Any, Dict, List, Optional

import redis

logger = logging.getLogger(__name__)


class CacheService:
    def __init__(self, redis_url: str):
        self._memory: Dict[str, List[Dict[str, Any]]] = {}
        self._redis = None
        try:
            client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            self._redis = client
            logger.info("Redis cache connected")
        except Exception as exc:
            logger.warning("Redis unavailable, using in-memory cache: %s", exc)

    @property
    def is_redis_enabled(self) -> bool:
        return self._redis is not None

    async def append_subtitle(self, stream_id: str, subtitle: Dict[str, Any], limit: int = 50) -> None:
        if self._redis:
            key = f"stream:{stream_id}:subtitles"
            self._redis.lpush(key, json.dumps(subtitle, ensure_ascii=False, default=str))
            self._redis.ltrim(key, 0, limit - 1)
            self._redis.expire(key, 3600)
            return

        items = self._memory.setdefault(stream_id, [])
        items.insert(0, subtitle)
        del items[limit:]

    async def recent_subtitles(self, stream_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if self._redis:
            key = f"stream:{stream_id}:subtitles"
            raw_items = self._redis.lrange(key, 0, limit - 1)
            return [json.loads(item) for item in raw_items]

        return list(self._memory.get(stream_id, []))[:limit]

    async def set_stream(self, stream_id: str, payload: Dict[str, Any]) -> None:
        if self._redis:
            self._redis.set(f"stream:{stream_id}:info", json.dumps(payload, ensure_ascii=False, default=str), ex=3600)
            return
        self._memory[f"info:{stream_id}"] = [payload]

    async def get_stream(self, stream_id: str) -> Optional[Dict[str, Any]]:
        if self._redis:
            raw = self._redis.get(f"stream:{stream_id}:info")
            return json.loads(raw) if raw else None
        items = self._memory.get(f"info:{stream_id}", [])
        return items[0] if items else None
