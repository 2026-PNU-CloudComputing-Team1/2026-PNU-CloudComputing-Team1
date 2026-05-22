"""
translator 서비스 테스트 — 실제 Google Cloud Translation API + Redis 사용
GOOGLE_APPLICATION_CREDENTIALS 또는 secrets/gcp-key.json 필요
Redis가 없으면 Redis 의존 테스트를 skip
"""
import asyncio
import json
import os
import time

import pytest
import pytest_asyncio

import app


# ── 실제 GCP API 번역 테스트 ─────────────────────────────────────────────────

class TestTranslateText:
    def test_korean_to_english(self, gcp_client):
        result = app.translate_text(gcp_client, "안녕하세요", "en")
        assert isinstance(result, str)
        assert len(result) > 0
        # Google이 번역한 결과가 원문 그대로는 아님
        assert result != "안녕하세요"

    def test_korean_to_japanese(self, gcp_client):
        result = app.translate_text(gcp_client, "안녕하세요", "ja")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_korean_to_chinese(self, gcp_client):
        # "zh"는 내부에서 "zh-CN"으로 변환되어 간체 중국어로 번역
        result = app.translate_text(gcp_client, "안녕하세요", "zh")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_technical_term_translation(self, gcp_client):
        result = app.translate_text(gcp_client, "클라우드 컴퓨팅", "en")
        assert "cloud" in result.lower() or "computing" in result.lower()

    def test_longer_sentence(self, gcp_client):
        text = "실시간 자막 파이프라인을 통해 전 세계 시청자에게 다국어 자막을 제공합니다."
        result = app.translate_text(gcp_client, text, "en")
        assert isinstance(result, str)
        assert len(result) > 10


class TestTranslateAll:
    @pytest.mark.asyncio
    async def test_all_three_languages(self, gcp_client):
        translations = await app.translate_all(gcp_client, "안녕하세요", ["en", "zh", "ja"])
        assert set(translations.keys()) == {"en", "zh", "ja"}
        for lang, text in translations.items():
            assert isinstance(text, str) and len(text) > 0, f"{lang} 번역 결과가 비어있음"

    @pytest.mark.asyncio
    async def test_parallel_is_faster_than_sequential(self, gcp_client):
        text = "클라우드 컴퓨팅 수업 텀 프로젝트입니다."
        start = time.time()
        await app.translate_all(gcp_client, text, ["en", "zh", "ja"])
        elapsed = time.time() - start
        # 3개 언어 병렬 번역이 3초 안에 완료되어야 함 (순차라면 더 오래 걸림)
        assert elapsed < 3.0, f"병렬 번역이 너무 느림: {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_lang(self, gcp_client, monkeypatch):
        # 지원하지 않는 언어 코드는 원문을 그대로 반환 (fallback)
        original = "안녕하세요"
        translations = await app.translate_all(gcp_client, original, ["en", "xx_INVALID"])
        assert "en" in translations
        assert translations.get("xx_INVALID") == original


# ── Redis + GCP 통합 테스트 ───────────────────────────────────────────────────

class TestHandleSttResult:
    @pytest.mark.asyncio
    async def test_creates_vtt_for_all_languages(self, redis_client, gcp_client, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        monkeypatch.setattr(app, "TARGET_LANGS", ["en", "zh", "ja"])

        msg = {
            "segment_num": 0,
            "text": "안녕하세요",
            "start_pts": 0.0,
            "end_pts": 2.0,
            "ingested_at": time.time(),
        }
        await app.handle_stt_result(msg, redis_client, gcp_client)

        for lang in ["en", "zh", "ja"]:
            vtt_path = tmp_path / lang / "seg0000.vtt"
            assert vtt_path.exists(), f"{lang}/seg0000.vtt가 생성되지 않음"

    @pytest.mark.asyncio
    async def test_vtt_content_is_valid_webvtt(self, redis_client, gcp_client, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        monkeypatch.setattr(app, "TARGET_LANGS", ["en"])

        msg = {
            "segment_num": 1,
            "text": "클라우드 컴퓨팅",
            "start_pts": 2.0,
            "end_pts": 4.0,
            "ingested_at": time.time(),
        }
        await app.handle_stt_result(msg, redis_client, gcp_client)

        content = (tmp_path / "en" / "seg0001.vtt").read_text()
        assert content.startswith("WEBVTT")
        assert "00:00:02.000 --> 00:00:04.000" in content

    @pytest.mark.asyncio
    async def test_publishes_vtt_ready_for_each_lang(self, redis_client, gcp_client, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        monkeypatch.setattr(app, "TARGET_LANGS", ["en", "zh", "ja"])

        # vtt:ready 메시지 수신용 구독 설정
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("vtt:ready")
        await asyncio.sleep(0.1)  # 구독 확정 대기

        msg = {
            "segment_num": 2,
            "text": "실시간 자막입니다",
            "start_pts": 4.0,
            "end_pts": 6.0,
            "ingested_at": time.time(),
        }
        await app.handle_stt_result(msg, redis_client, gcp_client)

        received = []
        for _ in range(20):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if message:
                received.append(json.loads(message["data"]))
            if len(received) == 3:
                break

        await pubsub.unsubscribe("vtt:ready")

        assert len(received) == 3, f"vtt:ready 메시지 3개 기대, 실제: {len(received)}개"
        langs = {m["lang"] for m in received}
        assert langs == {"en", "zh", "ja"}

    @pytest.mark.asyncio
    async def test_vtt_ready_payload_fields(self, redis_client, gcp_client, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        monkeypatch.setattr(app, "TARGET_LANGS", ["en"])

        pubsub = redis_client.pubsub()
        await pubsub.subscribe("vtt:ready")
        await asyncio.sleep(0.1)

        ingested_at = time.time()
        msg = {
            "segment_num": 5,
            "text": "테스트",
            "start_pts": 10.0,
            "end_pts": 12.0,
            "ingested_at": ingested_at,
        }
        await app.handle_stt_result(msg, redis_client, gcp_client)

        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
        await pubsub.unsubscribe("vtt:ready")

        assert message is not None
        payload = json.loads(message["data"])
        assert payload["segment_num"] == 5
        assert payload["lang"] == "en"
        assert payload["vtt_path"].endswith("en/seg0005.vtt")
        assert isinstance(payload["subtitle_delay"], float)
        assert payload["subtitle_delay"] >= 0

    @pytest.mark.asyncio
    async def test_empty_text_skipped(self, redis_client, gcp_client, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        monkeypatch.setattr(app, "TARGET_LANGS", ["en"])

        msg = {
            "segment_num": 9,
            "text": "   ",  # 공백만 있는 텍스트
            "start_pts": 18.0,
            "end_pts": 20.0,
            "ingested_at": time.time(),
        }
        await app.handle_stt_result(msg, redis_client, gcp_client)

        assert not (tmp_path / "en" / "seg0009.vtt").exists()
