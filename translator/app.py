"""
translator: STT 결과를 구독해 Google Cloud Translation으로 번역하고
언어별 VTT 파일을 저장한 뒤 subtitle-pub에 완료를 알린다.

흐름:
  whisper → PUBLISH stt:results
         → translator (여기)
              → Google Cloud Translation (en/zh/ja 병렬)
              → /data/subtitles/{lang}/seg{num:04d}.vtt 저장
              → PUBLISH vtt:ready
         → subtitle-pub → playlist.m3u8 갱신
"""
import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis
from google.cloud import translate_v2 as translate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REDIS_URL    = os.getenv("REDIS_URL",    "redis://localhost:6379")
VTT_DIR      = os.getenv("VTT_DIR",      "/data/subtitles")
SOURCE_LANG  = os.getenv("SOURCE_LANG",  "ko")

# subtitle-pub의 TARGET_LANGS와 일치시켜야 playlist가 채워짐
TARGET_LANGS = os.getenv("TARGET_LANGS", "en,zh,ja").split(",")

# Google Cloud Translation API는 "zh"를 "zh-CN"으로 전달해야 간체 중국어로 번역
_GOOGLE_LANG = {"zh": "zh-CN"}


def _google_lang(lang: str) -> str:
    return _GOOGLE_LANG.get(lang, lang)


def pts_to_vtt_ts(pts: float) -> str:
    """초 단위 PTS를 WebVTT 타임스탬프(HH:MM:SS.mmm)로 변환."""
    h   = int(pts // 3600)
    m   = int((pts % 3600) // 60)
    s   = pts % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def write_vtt(lang: str, segment_num: int, start_pts: float, end_pts: float, text: str) -> str:
    lang_dir = os.path.join(VTT_DIR, lang)
    os.makedirs(lang_dir, exist_ok=True)

    path = os.path.join(lang_dir, f"seg{segment_num:04d}.vtt")
    start_ts = pts_to_vtt_ts(start_pts)
    end_ts   = pts_to_vtt_ts(max(end_pts, start_pts + 0.1))

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"WEBVTT\n\n{start_ts} --> {end_ts}\n{text}\n")

    return path


def translate_text(client: translate.Client, text: str, target_lang: str) -> str:
    result = client.translate(
        text,
        source_language=SOURCE_LANG,
        target_language=_google_lang(target_lang),
    )
    return result["translatedText"]


async def translate_all(
    client: translate.Client,
    text: str,
    langs: list[str],
) -> dict[str, str]:
    """모든 언어를 asyncio.gather로 병렬 번역."""
    tasks = [
        asyncio.to_thread(translate_text, client, text, lang)
        for lang in langs
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    translations: dict[str, str] = {}
    for lang, result in zip(langs, results):
        if isinstance(result, Exception):
            log.warning(f"[translator] {lang} 번역 실패: {result}")
            translations[lang] = text  # 번역 실패 시 원문 fallback
        else:
            translations[lang] = result
    return translations


async def handle_stt_result(msg: dict, r: aioredis.Redis, client: translate.Client) -> None:
    segment_num = msg["segment_num"]
    text        = msg["text"].strip()
    start_pts   = float(msg.get("start_pts", 0.0))
    end_pts     = float(msg.get("end_pts", start_pts + 2.0))
    ingested_at = float(msg.get("ingested_at", time.time()))

    if not text:
        return

    log.info(f"[translator] seg{segment_num:04d} 번역 시작: '{text[:60]}'")

    translations = await translate_all(client, text, TARGET_LANGS)
    subtitle_delay = time.time() - ingested_at

    # ① backend의 WebSocket 브로드캐스트용 — 모든 언어를 한 번에 묶어 publish.
    #    backend가 이 채널을 구독해 SubtitleMessage 그대로 프론트로 전달.
    await r.publish(
        "subtitle:translated",
        json.dumps({
            "segment_num":    segment_num,
            "original_text":  text,
            "translations":   translations,
            "start_pts":      start_pts,
            "end_pts":        end_pts,
            "subtitle_delay": round(subtitle_delay, 2),
            "ingested_at":    ingested_at,
        }),
    )

    # ② subtitle-pub의 HLS playlist 갱신용 — 언어별 VTT 파일 + vtt:ready (기존 동작 유지).
    for lang, translated in translations.items():
        vtt_path = await asyncio.to_thread(
            write_vtt, lang, segment_num, start_pts, end_pts, translated
        )
        await r.publish(
            "vtt:ready",
            json.dumps({
                "segment_num":    segment_num,
                "lang":           lang,
                "vtt_path":       vtt_path,
                "subtitle_delay": round(subtitle_delay, 2),
            }),
        )
        log.info(
            f"[translator] seg{segment_num:04d} {lang} 완료 "
            f"| '{translated[:40]}' | delay={subtitle_delay:.1f}s"
        )


async def main() -> None:
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    await r.ping()
    log.info(f"[translator] Redis 연결 완료: {REDIS_URL}")

    # Google Cloud Translation 클라이언트
    # GOOGLE_APPLICATION_CREDENTIALS 환경변수 또는 마운트된 서비스 계정 JSON을 자동으로 사용
    client = translate.Client()
    log.info("[translator] Google Cloud Translation 클라이언트 초기화 완료")

    pubsub = r.pubsub()
    await pubsub.subscribe("stt:results")
    log.info("[translator] stt:results 구독 시작")

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            msg = json.loads(message["data"])
            await handle_stt_result(msg, r, client)
        except Exception as exc:
            log.error(f"[translator] 처리 중 오류: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
