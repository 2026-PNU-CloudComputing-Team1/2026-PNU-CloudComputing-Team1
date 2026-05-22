import glob
import json
import os
import subprocess
import time
import wave

import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
INPUT_URL = os.getenv("INPUT_URL", "/sample/demo.mp4")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/data/audio")
SEGMENT_DURATION = float(os.getenv("SEGMENT_DURATION", "2"))
REALTIME = os.getenv("REALTIME", "false").lower() == "true"


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def clean_output_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for path in glob.glob(os.path.join(OUTPUT_DIR, "segment_*.wav")):
        os.remove(path)


def extract_audio_segments() -> None:
    output_pattern = os.path.join(OUTPUT_DIR, "segment_%03d.wav")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        INPUT_URL,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "segment",
        "-segment_time",
        str(SEGMENT_DURATION),
        "-reset_timestamps",
        "1",
        output_pattern,
    ]

    print(f"[audio-extractor] input: {INPUT_URL}")
    subprocess.run(command, check=True)


def push_segments() -> None:
    client = redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()

    paths = sorted(glob.glob(os.path.join(OUTPUT_DIR, "segment_*.wav")))
    pushed = 0

    for index, path in enumerate(paths):
        duration = wav_duration(path)
        if abs(duration - SEGMENT_DURATION) > 0.1:
            print(f"[audio-extractor] skip {os.path.basename(path)}: {duration:.3f}s")
            continue

        payload = {
            "segment_num": pushed,
            "audio_path": path,
            "pts": pushed * SEGMENT_DURATION,
            "ingested_at": time.time(),
        }
        client.rpush("stt:queue", json.dumps(payload))
        print(f"[audio-extractor] queued seg{pushed:04d}: {os.path.basename(path)}")
        pushed += 1

        if REALTIME:
            time.sleep(SEGMENT_DURATION)

    print(f"[audio-extractor] done: {pushed} segments queued")


def main() -> None:
    clean_output_dir()
    extract_audio_segments()
    push_segments()


if __name__ == "__main__":
    main()
