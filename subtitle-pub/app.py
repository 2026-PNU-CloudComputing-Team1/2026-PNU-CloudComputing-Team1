"""
subtitle-pub: VTT 자막 플레이리스트 관리 서비스

translator가 PUBLISH한 vtt:ready 메시지를 구독해
언어별 HLS 자막 플레이리스트(playlist.m3u8)를 갱신하고
영상+자막 트랙 명세(master.m3u8)를 생성한다.
"""
import asyncio
import json
import logging
import os
from collections import defaultdict

import redis.asyncio as aioredis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
VTT_DIR = os.getenv("VTT_DIR", "/data/subtitles")
SEGMENT_DURATION = float(os.getenv("SEGMENT_DURATION", "2"))
PLAYLIST_SIZE = int(os.getenv("PLAYLIST_SIZE", "5"))

TARGET_LANGS = ["en", "zh", "ja"]
LANG_NAMES   = {"en": "English", "zh": "Chinese", "ja": "Japanese"}

# 언어별 최근 segment_num 목록 
# 슬라이딩 윈도우로 라이브 플레이리스트 유지
seg_history: dict[str, list[int]] = defaultdict(list)


def write_lang_playlist(lang: str) -> None:
    segs = seg_history[lang]
    if not segs:
        return

    lang_dir = os.path.join(VTT_DIR, lang)
    os.makedirs(lang_dir, exist_ok=True)

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{int(SEGMENT_DURATION)}",
        # EXT-X-MEDIA-SEQUENCE는 윈도우 첫 번째 세그먼트 번호여야 함
        # hls.js가 이 값으로 어디서부터 재생할지 계산
        f"#EXT-X-MEDIA-SEQUENCE:{segs[0]}",
        "",
    ]
    for seg_num in segs:
        lines.append(f"#EXTINF:{SEGMENT_DURATION:.3f},")
        lines.append(f"seg{seg_num:04d}.vtt")

    with open(os.path.join(lang_dir, "playlist.m3u8"), "w") as f:
        f.write("\n".join(lines) + "\n")


def write_master_playlist() -> None:
    # master.m3u8는 언어 목록이 고정이므로 시작 시 한 번만 생성
    # 세그먼트가 추가될 때마다 재생성할 필요 없음
    os.makedirs(VTT_DIR, exist_ok=True)

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "",
    ]
    for i, lang in enumerate(TARGET_LANGS):
        default = "YES" if i == 0 else "NO"
        lines.append(
            f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",'
            f'LANGUAGE="{lang}",NAME="{LANG_NAMES[lang]}",DEFAULT={default},'
            f'URI="/subtitles/{lang}/playlist.m3u8"'
        )
    lines += [
        "",
        '#EXT-X-STREAM-INF:BANDWIDTH=2800000,SUBTITLES="subs"',
        "/hls/live/stream/index.m3u8",
    ]

    with open(os.path.join(VTT_DIR, "master.m3u8"), "w") as f:
        f.write("\n".join(lines) + "\n")

    log.info("[subtitle-pub] master.m3u8 생성 완료")


async def handle_vtt_ready(msg: dict, r: aioredis.Redis) -> None:
    segment_num    = msg["segment_num"]
    lang           = msg["lang"]
    subtitle_delay = msg["subtitle_delay"]

    history = seg_history[lang]
    if segment_num not in history:
        history.append(segment_num)
        # 재전송이나 네트워크 지연으로 순서가 뒤바뀔 수 있어 항상 정렬
        history.sort()
        # 오래된 세그먼트 제거 — nginx가 이미 캐싱했으므로 플레이리스트에서만 제외
        if len(history) > PLAYLIST_SIZE:
            seg_history[lang] = history[-PLAYLIST_SIZE:]

    write_lang_playlist(lang)

    # LPUSH로 최신 항목을 앞에 쌓고 LTRIM으로 메모리 바운드 유지 (최대 100개)
    await r.lpush(
        "metrics:subtitle_delay",
        json.dumps({"lang": lang, "segment_num": segment_num, "delay": subtitle_delay}),
    )
    await r.ltrim("metrics:subtitle_delay", 0, 99)

    log.info(
        f"[subtitle-pub] seg{segment_num:04d} {lang} 플레이리스트 갱신 "
        f"| delay={subtitle_delay:.1f}s | 윈도우={seg_history[lang]}"
    )


async def main() -> None:
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    await r.ping()
    log.info(f"[subtitle-pub] Redis 연결 완료: {REDIS_URL}")

    # 구독 시작 전에 master.m3u8을 먼저 생성
    write_master_playlist()

    pubsub = r.pubsub()
    await pubsub.subscribe("vtt:ready")
    log.info("[subtitle-pub] vtt:ready 구독 시작")

    async for message in pubsub.listen():
        # subscribe/unsubscribe 확인 메시지 필터링
        if message["type"] != "message":
            continue
        msg = json.loads(message["data"])
        await handle_vtt_ready(msg, r)


if __name__ == "__main__":
    asyncio.run(main())
