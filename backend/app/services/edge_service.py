"""Edge container management — 데모용 페일오버 시뮬레이션.

프론트의 "Kill Edge" 버튼이 docker stop/start로 실제 컨테이너를 제어한다.
docker.sock을 마운트해야 동작하며, 데모 환경 전용 (프로덕션 금지).
"""
import logging
from typing import Dict

import docker
from docker.errors import DockerException, NotFound

log = logging.getLogger(__name__)

# 프론트 edge id → docker-compose 서비스명
# compose가 프로젝트명_서비스명_1 형식으로 컨테이너를 만들지만,
# docker SDK는 서비스명만으로도 찾을 수 있도록 label 기반 lookup 사용
EDGE_CONTAINER_MAP: Dict[str, str] = {
    "kr": "edge-korea",
    "jp": "edge-japan",
    "cn": "edge-china",
    "us": "edge-us",
}


class EdgeService:
    def __init__(self) -> None:
        try:
            self._client = docker.from_env()
            self._client.ping()
            self._available = True
            log.info("[edge-service] docker socket connected")
        except DockerException as exc:
            self._client = None
            self._available = False
            log.warning("[edge-service] docker socket unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    def _find_container(self, edge_id: str):
        service_name = EDGE_CONTAINER_MAP.get(edge_id)
        if not service_name or not self._client:
            return None
        # compose v2 라벨로 정확히 매칭 — 같은 이미지 다른 프로젝트 충돌 방지
        containers = self._client.containers.list(
            all=True,
            filters={"label": f"com.docker.compose.service={service_name}"},
        )
        return containers[0] if containers else None

    def status_all(self) -> Dict[str, dict]:
        result = {}
        for edge_id in EDGE_CONTAINER_MAP:
            container = self._find_container(edge_id) if self._available else None
            if container is None:
                result[edge_id] = {"running": False, "state": "unknown"}
            else:
                result[edge_id] = {
                    "running": container.status == "running",
                    "state": container.status,
                }
        return result

    def stop(self, edge_id: str) -> dict:
        container = self._find_container(edge_id)
        if container is None:
            raise NotFound(f"edge container not found: {edge_id}")
        container.stop(timeout=2)
        return {"edge_id": edge_id, "state": "stopped"}

    def start(self, edge_id: str) -> dict:
        container = self._find_container(edge_id)
        if container is None:
            raise NotFound(f"edge container not found: {edge_id}")
        container.start()
        return {"edge_id": edge_id, "state": "started"}
