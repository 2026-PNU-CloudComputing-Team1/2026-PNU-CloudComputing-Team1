"""
subtitle-pub 서비스 테스트 — 실제 Redis 필요
REDIS_URL 환경변수로 대상 지정 (기본값: redis://localhost:6380)
Redis가 없으면 자동 skip
"""
import json
import os
import pytest
import pytest_asyncio
import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380")

import app


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


@pytest.fixture(autouse=True)
def reset_seg_history():
    app.seg_history.clear()


@pytest.mark.asyncio
async def test_handle_vtt_ready_creates_playlist(redis_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))

    msg = {"segment_num": 0, "lang": "en", "vtt_path": "/data/subtitles/en/seg0000.vtt", "subtitle_delay": 3.5}
    await app.handle_vtt_ready(msg, redis_client)

    playlist = tmp_path / "en" / "playlist.m3u8"
    assert playlist.exists()
    content = playlist.read_text()
    assert "seg0000.vtt" in content
    assert "#EXT-X-MEDIA-SEQUENCE:0" in content


@pytest.mark.asyncio
async def test_handle_vtt_ready_pushes_metrics(redis_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
    # 테스트 전 메트릭 초기화
    await redis_client.delete("metrics:subtitle_delay")

    msg = {"segment_num": 0, "lang": "en", "vtt_path": "/data/subtitles/en/seg0000.vtt", "subtitle_delay": 4.2}
    await app.handle_vtt_ready(msg, redis_client)

    entries = await redis_client.lrange("metrics:subtitle_delay", 0, -1)
    assert len(entries) == 1
    data = json.loads(entries[0])
    assert data["lang"] == "en"
    assert data["delay"] == 4.2


@pytest.mark.asyncio
async def test_metrics_bounded_to_100(redis_client, tmp_path, monkeypatch):
    # 100개 초과 시 LTRIM으로 오래된 항목 제거되는지 확인
    monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
    await redis_client.delete("metrics:subtitle_delay")

    for i in range(110):
        msg = {"segment_num": i, "lang": "en", "vtt_path": f"/data/subtitles/en/seg{i:04d}.vtt", "subtitle_delay": 1.0}
        await app.handle_vtt_ready(msg, redis_client)

    count = await redis_client.llen("metrics:subtitle_delay")
    assert count == 100


@pytest.mark.asyncio
async def test_sliding_window_drops_oldest(redis_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
    monkeypatch.setattr(app, "PLAYLIST_SIZE", 3)

    for i in range(4):
        msg = {"segment_num": i, "lang": "en", "vtt_path": f"/data/subtitles/en/seg{i:04d}.vtt", "subtitle_delay": 1.0}
        await app.handle_vtt_ready(msg, redis_client)

    # seg0000이 윈도우에서 밀려났는지 확인
    assert 0 not in app.seg_history["en"]
    assert app.seg_history["en"] == [1, 2, 3]

    content = (tmp_path / "en" / "playlist.m3u8").read_text()
    assert "seg0000.vtt" not in content
    assert "#EXT-X-MEDIA-SEQUENCE:1" in content


@pytest.mark.asyncio
async def test_duplicate_segment_ignored(redis_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))

    msg = {"segment_num": 0, "lang": "en", "vtt_path": "/data/subtitles/en/seg0000.vtt", "subtitle_delay": 1.0}
    await app.handle_vtt_ready(msg, redis_client)
    await app.handle_vtt_ready(msg, redis_client)  # 중복 전송

    assert app.seg_history["en"].count(0) == 1
