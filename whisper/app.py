import os
import json
import math
import struct
import tempfile
import time
import wave
import logging

# Redis 클라이언트: 작업 큐 수신 및 결과 발행에 사용
import redis

from faster_whisper import WhisperModel

# 로그 포맷 설정: 시:분:초 메시지 형식으로 출력 ([whisper]는 각 메시지에 직접 포함)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# tiny/base/small/medium/large 중 선택
# 일단 base 모델로 테스트, 성능 부족하면 small로 올리기
# medium/large는 CPU에서 처리 불가
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
# 항상 한국어 음성으로 감지
SOURCE_LANG = os.getenv("SOURCE_LANG", "ko")

# MediaMTX HLS 세그먼트 길이와 일치시켜야 end_pts가 정확함
SEGMENT_DURATION = float(os.getenv("SEGMENT_DURATION", "2"))

# 버퍼가 이 길이를 초과하면 묵음 없이도 강제로 flush
BUFFER_MAX_SEC = float(os.getenv("BUFFER_MAX_SEC", "10"))

# 이 RMS 값 미만이면 묵음으로 판단해 버퍼를 flush하는 트리거로 사용
SILENCE_RMS_THRESHOLD = int(os.getenv("SILENCE_RMS_THRESHOLD", "500"))


def check_wav_duration(audio_path: str) -> None:
    # HLS 세그먼트가 SEGMENT_DURATION과 다르면 end_pts가 틀어짐
    with wave.open(audio_path, "rb") as wf:
        actual = wf.getnframes() / wf.getframerate()
    if abs(actual - SEGMENT_DURATION) > 0.1:
        raise ValueError(
            f"[whisper] WAV 길이 불일치: 기대={SEGMENT_DURATION}s, 실제={actual:.3f}s ({audio_path})"
        )


def detect_silence(audio_path: str) -> bool:
    # 16-bit PCM WAV의 샘플을 읽어 RMS 에너지 계산
    with wave.open(audio_path, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    # 2바이트씩 signed short로 언패킹
    samples = struct.unpack(f"{len(frames) // 2}h", frames)
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    # RMS가 임계값 미만이면 묵음으로 판단, 버퍼 flush 트리거
    return rms < SILENCE_RMS_THRESHOLD


def merge_wavs(audio_paths: list[str]) -> str:
    # 버퍼에 쌓인 여러 WAV 조각을 임시 파일 하나로 이어 붙임
    # 첫 번째 파일의 헤더를 기준으로 사용
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


def load_model() -> WhisperModel:
    # Whisper 모델을 CPU에 int8 양자화로 로드 (속도/메모리 절약)
    log.info(f"[whisper] 모델 로드 중: {WHISPER_MODEL}")
    model = WhisperModel(
        WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
    )
    log.info("[whisper] 모델 로드 완료")
    return model


def transcribe(model: WhisperModel, audio_path: str) -> list:
    segments, _ = model.transcribe(
        audio_path,
        language=SOURCE_LANG,
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    
    return list(segments)


def flush_buffer(model: WhisperModel, r: redis.Redis, buffer: list) -> None:
    if not buffer:
        return

    first_seg_num = buffer[0]["segment_num"]
    last_seg_num  = buffer[-1]["segment_num"]
    base_pts      = buffer[0]["pts"]
    ingested_at   = buffer[0]["ingested_at"]

    # 버퍼의 WAV 조각들을 하나로 병합해 Whisper에 넘김
    merged_path = merge_wavs([job["audio_path"] for job in buffer])

    try:
        segments = transcribe(model, merged_path)

        published = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            # Whisper가 반환한 seg.start/end는 병합 파일 기준이므로 base_pts를 더해 절대 시간으로 변환
            result = {
                "segment_num": first_seg_num,
                "text":        text,
                "start_pts":   base_pts + seg.start,
                "end_pts":     base_pts + seg.end,
                "ingested_at": ingested_at,
            }
            r.publish("stt:results", json.dumps(result))
            published.append(text)

        if not published:
            log.info(f"[whisper] seg{first_seg_num:04d}~{last_seg_num:04d}: 묵음 구간, 스킵")
            return

        stt_delay = time.time() - ingested_at
        log.info(
            f"[whisper] seg{first_seg_num:04d}~{last_seg_num:04d} 완료 "
            f"| '{' '.join(published)[:60]}' | {stt_delay:.1f}s"
        )
    finally:
        # 성공/실패 관계없이 임시 병합 파일 삭제
        os.unlink(merged_path)


def connect_redis() -> redis.Redis:
    # Redis에 접속하고 ping으로 연결 확인
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    log.info(f"[whisper] Redis 연결 완료: {REDIS_URL}")
    return r


def main():
    model = load_model()
    r = connect_redis()

    log.info("[whisper] stt:queue 대기 중")

    buffer: list[dict] = []
    buffer_duration = 0.0  # 현재 버퍼에 쌓인 총 오디오 길이 (초)

    while True:
        # BLPOP: 새 항목이 들어올 때까지 최대 5초 블로킹 대기
        item = r.blpop("stt:queue", timeout=5)

        if item is None:
            # 5초간 새 항목 없음 → 버퍼에 남은 데이터를 강제 flush해 지연 방지
            if buffer:
                log.info(f"[whisper] 큐 타임아웃, 버퍼 강제 전송 ({buffer_duration:.1f}s)")
                flush_buffer(model, r, buffer)
                buffer = []
                buffer_duration = 0.0
            continue

        # item = (큐 이름, JSON 문자열) 튜플
        _, raw = item
        job = json.loads(raw)
        job.setdefault("ingested_at", time.time())

        audio_path = job["audio_path"]
        seg_num    = job["segment_num"]

        # 파일이 존재하지 않으면 스킵
        if not os.path.exists(audio_path):
            log.warning(f"[whisper] 파일 없음, 스킵: {audio_path}")
            continue

        # HLS 세그먼트 길이 검증
        check_wav_duration(audio_path)

        buffer.append(job)
        buffer_duration += SEGMENT_DURATION

        silence = detect_silence(audio_path)
        timeout = buffer_duration >= BUFFER_MAX_SEC

        if silence or timeout:
            # 묵음이 끝나는 지점 또는 최대 길이 도달 시 버퍼를 Whisper에 넘김
            reason = "묵음 감지" if silence else f"최대 길이 초과 ({buffer_duration:.1f}s)"
            log.info(
                f"[whisper] 버퍼 전송: seg{buffer[0]['segment_num']:04d}~{seg_num:04d} ({reason})"
            )
            flush_buffer(model, r, buffer)
            buffer = []
            buffer_duration = 0.0


if __name__ == "__main__":
    main()
