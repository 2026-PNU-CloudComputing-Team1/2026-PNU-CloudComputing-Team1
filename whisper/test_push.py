"""
whisper 서비스 단독 테스트
실제 audio-extractor 없이 WAV 파일을 stt:queue에 넣음.
"""

import os
import sys
import json
import time
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380")


def push(audio_path: str, seg_num: int = 0):
    r = redis.from_url(REDIS_URL, decode_responses=True)

    payload = json.dumps({
        "segment_num": seg_num,
        "audio_path":  audio_path,
        "pts":         float(seg_num * 2),
        "ingested_at": time.time(),
    })

    r.rpush("stt:queue", payload)
    print(f"[push] stt:queue ← seg{seg_num:04d} ({audio_path})")

    # stt:results 채널을 구독해서 응답 확인
    pubsub = r.pubsub()
    pubsub.subscribe("stt:results")
    print("[대기] stt:results 채널 구독 중...")

    for msg in pubsub.listen():
        if msg["type"] != "message":
            continue
        result = json.loads(msg["data"])
        print("\n[결과]")
        print(f"  segment_num : {result['segment_num']}")
        print(f"  text        : {result['text']}")
        print(f"  start_pts   : {result['start_pts']}s")
        print(f"  end_pts     : {result['end_pts']}s")
        break


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    push(sys.argv[1])
