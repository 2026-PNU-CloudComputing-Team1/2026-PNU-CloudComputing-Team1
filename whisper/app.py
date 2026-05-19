import os
import json
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

# Whisper 모델
# tiny/base/small/medium/large 다섯 가지의 모델 중 일단 base 사용해보고
# 성능이 안 좋으면 small로 변경 가능
# medium/large는 CPU에서 처리하기 어려워서 사용하면 안 됨
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# 전사할 음성의 언어 (항상 한국어 음성 감지)
SOURCE_LANG = os.getenv("SOURCE_LANG", "ko")

# MediaMTX의 HLS 세그먼트 길이와 일치 (기본 2초)
SEGMENT_DURATION = float(os.getenv("SEGMENT_DURATION", "2"))


def check_wav_duration(audio_path: str) -> None:
    # HLS 세그먼트가 SEGMENT_DURATION과 다르면 end_pts가 틀어짐
    with wave.open(audio_path, "rb") as wf:
        actual = wf.getnframes() / wf.getframerate()
    if abs(actual - SEGMENT_DURATION) > 0.1:
        raise ValueError(
            f"[whisper] WAV 길이 불일치: 기대={SEGMENT_DURATION}s, 실제={actual:.3f}s ({audio_path})"
        )


def load_model() -> WhisperModel:
    # Whisper 모델을 CPU에 int8 양자화로 로드
    log.info(f"[whisper] 모델 로드 중: {WHISPER_MODEL}")
    model = WhisperModel(
        WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
    )
    log.info("[whisper] 모델 로드 완료")
    return model


def transcribe(model: WhisperModel, audio_path: str) -> str:
    # WAV 파일을 받아 텍스트를 반환
    # beam_size=1 이 가장 빠름 (greedy decoding)
    # 묵음 구간은 처리 건너뜀 (속도 향상)
    segments, _ = model.transcribe(
        audio_path,
        language=SOURCE_LANG,
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )

    # 각 세그먼트의 텍스트를 공백으로 이어붙여 하나의 문자열로 반환
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text


def connect_redis() -> redis.Redis:
    # Redis에 접속하고 ping으로 연결 확인
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    log.info(f"[whisper] Redis 연결 완료: {REDIS_URL}")
    return r


def main():
    # 모델과 Redis 연결을 초기화
    model = load_model()
    r = connect_redis()

    log.info("[whisper] stt:queue 대기 중")

    while True:
        # BLPOP: stt:queue에 항목이 생길 때까지 최대 5초 블로킹 대기
        item = r.blpop("stt:queue", timeout=5)
        # timeout 시 다시 대기
        if item is None:
            continue

        # item = (큐 이름, JSON 문자열) 튜플
        _, raw = item
        job = json.loads(raw)
        # 세그먼트 순번, 전사할 WAV 파일 경로, 스트림 시작 기준 절대 시간(초), 지연 측정용 수신 시각 포함
        seg_num = job["segment_num"]
        audio_path = job["audio_path"]
        pts = job["pts"]
        ingested_at = job.get("ingested_at", time.time())

        log.info(f"[whisper] 처리 시작: seg{seg_num:04d} ({audio_path})")

        # 파일이 존재하지 않으면 스킵
        if not os.path.exists(audio_path):
            log.warning(f"[whisper] 파일 없음, 스킵: {audio_path}")
            continue

        # WAV 파일이 HLS 세그먼트 길이와 맞는지 검증
        check_wav_duration(audio_path)

        # Whisper 모델로 음성을 텍스트 변환
        text = transcribe(model, audio_path)

        # 변환 결과가 없으면 묵음 구간으로 판단하고 스킵
        if not text:
            log.info(f"[whisper] seg{seg_num:04d}: 묵음 구간, 스킵")
            continue

        # 전사 결과를 translator가 구독 중인 stt:results 채널로 발행
        result = {
            "segment_num": seg_num,
            "text":        text,
            "start_pts":   pts,
            "end_pts":     pts + SEGMENT_DURATION, # 세그먼트 종료 시각
            "ingested_at": ingested_at, # 지연 측정용 타임스탬프
        }
        r.publish("stt:results", json.dumps(result))

        # 수신 시각부터 발행 시각까지의 처리 지연 시간 로깅
        stt_delay = time.time() - ingested_at
        log.info(f"[whisper] seg{seg_num:04d} 완료 | 텍스트: '{text[:60]}' | 처리 시간: {stt_delay:.1f}s")


if __name__ == "__main__":
    main()
