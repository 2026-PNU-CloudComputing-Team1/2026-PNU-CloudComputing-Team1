import os
import sys

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# secrets/gcp-key.json 경로를 자동으로 설정 (이미 환경변수가 있으면 그대로 사용)
_KEY_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../secrets/gcp-key.json")
)
if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ and os.path.exists(_KEY_PATH):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _KEY_PATH

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


@pytest_asyncio.fixture
async def redis_client():
    """Redis 연결 — 접속 실패 시 테스트 전체 skip."""
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await r.ping()
    except Exception:
        pytest.skip(f"Redis 연결 불가: {REDIS_URL}")
    yield r
    await r.aclose()


@pytest.fixture
def gcp_client():
    """Google Cloud Translation 클라이언트 — 키 없으면 skip."""
    from google.cloud import translate_v2 as translate
    try:
        client = translate.Client()
        # 간단한 API 호출로 인증 확인
        client.get_languages()
        return client
    except Exception as exc:
        pytest.skip(f"Google Cloud Translation 초기화 실패: {exc}")
