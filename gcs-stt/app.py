import json
import logging
import os
import subprocess
import time

import redis
from google.cloud import speech

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REDIS_URL   = os.getenv("REDIS_URL", "redis://localhost:6379")
RTMP_URL    = os.getenv("RTMP_URL", "rtmp://mediamtx:1935/live/smooth")
SOURCE_LANG = os.getenv("SOURCE_LANG", "ko-KR")
STREAM_ID   = os.getenv("STREAM_ID", "demo")

SAMPLE_RATE  = 16000
CHUNK_MS     = 100
CHUNK_BYTES  = int(SAMPLE_RATE * 2 * CHUNK_MS / 1000)  # 3200 bytes per 100ms chunk

# GCS STT Streaming 최대 5분 제한 → 4분마다 재연결
MAX_STREAM_SEC = 240


def connect_redis() -> redis.Redis:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    log.info("[gcs-stt] Redis 연결 완료: %s", REDIS_URL)
    return r


def audio_generator(process: subprocess.Popen, deadline: float):
    while time.time() < deadline:
        chunk = process.stdout.read(CHUNK_BYTES)
        if not chunk:
            break
        yield speech.StreamingRecognizeRequest(audio_content=chunk)


def run_once(client: speech.SpeechClient, r: redis.Redis, seg_counter: list) -> None:
    log.info("[gcs-stt] RTMP 연결 시도: %s", RTMP_URL)

    process = subprocess.Popen(
        [
            "ffmpeg",
            "-loglevel", "error",
            "-i", RTMP_URL,
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    config = speech.StreamingRecognitionConfig(
        config=speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=SAMPLE_RATE,
            language_code=SOURCE_LANG,
            enable_automatic_punctuation=True,
        ),
        interim_results=True,
    )

    deadline    = time.time() + MAX_STREAM_SEC
    stream_start = time.time()

    try:
        responses = client.streaming_recognize(config, audio_generator(process, deadline))
        log.info("[gcs-stt] 스트리밍 인식 시작")

        for response in responses:
            for result in response.results:
                if not result.alternatives:
                    continue
                text = result.alternatives[0].transcript.strip()
                if not text:
                    continue

                if result.is_final:
                    now     = time.time()
                    elapsed = now - stream_start
                    seg_num = seg_counter[0]
                    seg_counter[0] += 1

                    r.publish("stt:results", json.dumps({
                        "segment_num": seg_num,
                        "text":        text,
                        "start_pts":   round(elapsed, 3),
                        "end_pts":     round(elapsed + 2.0, 3),
                        "ingested_at": now,
                    }))
                    log.info("[gcs-stt] seg%04d 완료 | '%s'", seg_num, text[:60])
                else:
                    r.publish("stt:interim", json.dumps({
                        "text":      text,
                        "stream_id": STREAM_ID,
                    }))

    except Exception as exc:
        log.warning("[gcs-stt] 스트리밍 중단: %s", exc)
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    client = speech.SpeechClient()
    log.info("[gcs-stt] Google Cloud Speech-to-Text 클라이언트 초기화 완료")

    r = connect_redis()
    seg_counter = [0]

    while True:
        try:
            run_once(client, r, seg_counter)
        except Exception as exc:
            log.warning("[gcs-stt] 오류 발생: %s", exc)
        log.info("[gcs-stt] 3초 후 재연결...")
        time.sleep(3)


if __name__ == "__main__":
    main()
