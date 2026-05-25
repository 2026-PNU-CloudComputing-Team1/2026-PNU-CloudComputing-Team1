import os
import json
import math
import re
import struct
import tempfile
import time
import wave
import logging

import redis
from google.cloud import speech

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379")
SOURCE_LANG      = os.getenv("SOURCE_LANG", "ko-KR")
SEGMENT_DURATION = float(os.getenv("SEGMENT_DURATION", "1.5"))
BUFFER_MAX_SEC   = float(os.getenv("BUFFER_MAX_SEC", "3"))
SILENCE_RMS_THRESHOLD = int(os.getenv("SILENCE_RMS_THRESHOLD", "120"))
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.6"))
MIN_TEXT_CHARS   = int(os.getenv("MIN_TEXT_CHARS", "3"))


def detect_silence(audio_path: str) -> bool:
    with wave.open(audio_path, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    samples = struct.unpack(f"{len(frames) // 2}h", frames)
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return rms < SILENCE_RMS_THRESHOLD


def check_wav_duration(audio_path: str) -> bool:
    with wave.open(audio_path, "rb") as wf:
        actual = wf.getnframes() / wf.getframerate()
    if abs(actual - SEGMENT_DURATION) > 0.1:
        log.warning(f"[gcs-stt] WAV 길이 불일치, 스킵: 기대={SEGMENT_DURATION}s, 실제={actual:.3f}s")
        return False
    return True


def merge_wavs(audio_paths: list[str]) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    with wave.open(audio_paths[0], "rb") as first:
        params = first.getparams()

    with wave.open(tmp.name, "wb") as out:
        out.setparams(params)
        for path in audio_paths:
            with wave.open(path, "rb") as wf:
                out.writeframes(wf.readframes(wf.getnframes()))

    return tmp.name


def existing_jobs(buffer: list[dict]) -> list[dict]:
    existing = [job for job in buffer if os.path.exists(job["audio_path"])]
    dropped = len(buffer) - len(existing)
    if dropped:
        log.warning(f"[gcs-stt] 사라진 오디오 조각 {dropped}개 스킵")
    return existing


def is_publishable(text: str) -> bool:
    meaningful = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    return len(meaningful) >= MIN_TEXT_CHARS


def has_repetition_loop(text: str) -> bool:
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", text)
    if not tokens:
        return False
    for token in set(tokens):
        if len(token) >= 2 and tokens.count(token) >= 4:
            return True
    bigrams = [" ".join(tokens[i:i + 2]) for i in range(len(tokens) - 1)]
    return any(bigrams.count(b) >= 3 for b in set(bigrams))


def transcribe(client: speech.SpeechClient, audio_path: str) -> list[str]:
    with open(audio_path, "rb") as f:
        content = f.read()

    audio = speech.RecognitionAudio(content=content)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code=SOURCE_LANG,
        audio_channel_count=1,
        enable_automatic_punctuation=True,
    )

    response = client.recognize(config=config, audio=audio)

    results = []
    for result in response.results:
        if not result.alternatives:
            continue
        alt = result.alternatives[0]
        if alt.confidence >= MIN_CONFIDENCE:
            results.append(alt.transcript.strip())

    return results


def flush_buffer(client: speech.SpeechClient, r: redis.Redis, buffer: list) -> None:
    buffer = existing_jobs(buffer)
    if not buffer:
        return

    first_seg_num = buffer[0]["segment_num"]
    last_seg_num  = buffer[-1]["segment_num"]
    base_pts      = buffer[0]["pts"]
    ingested_at   = buffer[0]["ingested_at"]

    merged_path = merge_wavs([job["audio_path"] for job in buffer])

    try:
        texts = transcribe(client, merged_path)
        published = [t for t in texts if is_publishable(t)]

        if not published:
            log.info(f"[gcs-stt] seg{first_seg_num:04d}~{last_seg_num:04d}: 신뢰도 미달, 스킵")
            return

        combined = re.sub(r"\s+", " ", " ".join(published)).strip()

        if has_repetition_loop(combined):
            log.info(f"[gcs-stt] seg{first_seg_num:04d}~{last_seg_num:04d}: 반복 환각 의심, 스킵")
            return

        result = {
            "segment_num": first_seg_num,
            "text": combined,
            "start_pts": base_pts,
            "end_pts": base_pts + (len(buffer) * SEGMENT_DURATION),
            "ingested_at": ingested_at,
        }
        r.publish("stt:results", json.dumps(result))

        stt_delay = time.time() - ingested_at
        log.info(
            f"[gcs-stt] seg{first_seg_num:04d}~{last_seg_num:04d} 완료 "
            f"| '{combined[:60]}' | {stt_delay:.1f}s"
        )
    finally:
        os.unlink(merged_path)


def connect_redis() -> redis.Redis:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    log.info(f"[gcs-stt] Redis 연결 완료: {REDIS_URL}")
    return r


def main():
    client = speech.SpeechClient()
    log.info("[gcs-stt] Google Cloud Speech-to-Text 클라이언트 초기화 완료")

    r = connect_redis()
    log.info("[gcs-stt] stt:queue 대기 중")

    buffer: list[dict] = []
    buffer_duration = 0.0

    while True:
        item = r.blpop("stt:queue", timeout=5)

        if item is None:
            if buffer:
                log.info(f"[gcs-stt] 큐 타임아웃, 버퍼 강제 전송 ({buffer_duration:.1f}s)")
                flush_buffer(client, r, buffer)
                buffer = []
                buffer_duration = 0.0
            continue

        _, raw = item
        job = json.loads(raw)
        job.setdefault("ingested_at", time.time())

        audio_path = job["audio_path"]

        if not os.path.exists(audio_path):
            log.warning(f"[gcs-stt] 파일 없음, 스킵: {audio_path}")
            continue

        if not check_wav_duration(audio_path):
            continue

        buffer.append(job)
        buffer_duration += SEGMENT_DURATION

        silence = detect_silence(audio_path)
        timeout = buffer_duration >= BUFFER_MAX_SEC

        if silence or timeout:
            seg_num = job["segment_num"]
            reason = "묵음 감지" if silence else f"최대 길이 초과 ({buffer_duration:.1f}s)"
            log.info(
                f"[gcs-stt] 버퍼 전송: seg{buffer[0]['segment_num']:04d}~{seg_num:04d} ({reason})"
            )
            flush_buffer(client, r, buffer)
            buffer = []
            buffer_duration = 0.0


if __name__ == "__main__":
    main()
