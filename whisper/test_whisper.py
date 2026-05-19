"""
faster-whisper STT 단위 테스트
transcribe() 함수만 직접 테스트
"""

import sys
import time
import wave
import struct
import tempfile
import os

from app import load_model, transcribe


def make_silent_wav(duration: float = 2.0, sample_rate: int = 16000) -> str:
    # 묵음 WAV 파일을 임시 경로에 생성하고 경로를 반환
    n_samples = int(duration * sample_rate)
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))

    return path


def run_test(label: str, model, audio_path: str):
    print(f"\n{'─' * 50}")
    print(f"테스트: {label}")
    print(f"파일  : {audio_path}")

    start = time.time()
    result = transcribe(model, audio_path)
    transcription_time = time.time() - start

    print(f"결과  : '{result}' " + ("(묵음)" if not result else ""))
    print(f"소요 시간: {transcription_time:.2f}s")


def main():
    model = load_model()

    # 케이스 1: 묵음 처리 검증
    # vad_filter=True 이므로 묵음 구간은 처리하지 않고 빈 문자열 반환해야 함
    silent_wav = make_silent_wav()
    try:
        run_test("묵음 2초", model, silent_wav)
    finally:
        os.remove(silent_wav)

    # 케이스 2: 실제 음성 파일
    if len(sys.argv) > 1:
        audio_path = sys.argv[1]
        if not os.path.exists(audio_path):
            print(f"\n파일을 찾을 수 없음: {audio_path}")
            sys.exit(1)
        run_test("실제 음성 파일", model, audio_path)


if __name__ == "__main__":
    main()
