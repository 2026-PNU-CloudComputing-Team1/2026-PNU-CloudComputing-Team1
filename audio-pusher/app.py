import glob
import json
import os
import time
import wave

import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
SAMPLE_DIR = os.getenv("SAMPLE_DIR", "/sample")
SEGMENT_DURATION = float(os.getenv("SEGMENT_DURATION", "2"))
REALTIME = os.getenv("REALTIME", "true").lower() == "true"


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def main() -> None:
    client = redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()

    paths = sorted(glob.glob(os.path.join(SAMPLE_DIR, "segment_*.wav")))
    valid_paths = []

    for path in paths:
        duration = wav_duration(path)
        if abs(duration - SEGMENT_DURATION) > 0.1:
            print(f"[audio-pusher] skip {os.path.basename(path)}: {duration:.3f}s")
            continue
        valid_paths.append(path)

    print(f"[audio-pusher] push {len(valid_paths)} wav segments to stt:queue")

    for index, path in enumerate(valid_paths):
        payload = {
            "segment_num": index,
            "audio_path": path,
            "pts": index * SEGMENT_DURATION,
            "ingested_at": time.time(),
        }
        client.rpush("stt:queue", json.dumps(payload))
        print(f"[audio-pusher] queued seg{index:04d}: {os.path.basename(path)}")

        if REALTIME:
            time.sleep(SEGMENT_DURATION)

    print("[audio-pusher] done")


if __name__ == "__main__":
    main()
