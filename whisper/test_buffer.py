"""
버퍼 기반 전사 통합 테스트
segment_000 ~ segment_021을 stt:queue에 순서대로 넣고
stt:results로 돌아오는 결과를 출력한다.
"""

import os
import sys
import json
import time
import threading
import redis

REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6380")
SEGMENT_DURATION = 2.0   # app.py의 SEGMENT_DURATION과 맞춰야 pts가 정확함
TOTAL_SEGMENTS   = 22    # 000 ~ 021


def push_segments(sample_dir: str, container_dir: str) -> None:
    # push 전용 연결 (pubsub 연결과 분리)
    r = redis.from_url(REDIS_URL, decode_responses=True)
    for i in range(TOTAL_SEGMENTS):
        local_path = os.path.join(sample_dir, f"segment_{i:03d}.wav")
        # 큐에 넣는 경로는 whisper 컨테이너 기준 경로여야 함
        audio_path = os.path.join(container_dir, f"segment_{i:03d}.wav")

        if not os.path.exists(local_path):
            print(f"[push] 파일 없음, 스킵: {audio_path}")
            continue

        payload = json.dumps({
            "segment_num": i,
            "audio_path":  audio_path,
            "pts":         float(i * SEGMENT_DURATION),
            "ingested_at": time.time(),
        })
        r.rpush("stt:queue", payload)
        print(f"[push] seg{i:03d} → stt:queue ({audio_path})")

        # 실제 HLS 스트리밍과 동일한 간격으로 전송
        time.sleep(SEGMENT_DURATION)

    print(f"\n[push] 전체 {TOTAL_SEGMENTS}개 전송 완료")


def listen_results(stop_event: threading.Event, timeout: int = 300) -> None:
    # 수신 전용 연결 (push 연결과 분리)
    r = redis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe("stt:results")
    print("[결과] stt:results 구독 중...\n")

    received = 0
    deadline = time.time() + timeout

    try:
        while not stop_event.is_set():
            if time.time() > deadline:
                print(f"\n[결과] 타임아웃 ({timeout}s), 종료")
                break

            # 1초마다 메시지 폴링 (블로킹 없이 stop_event 체크 가능)
            msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue

            result = json.loads(msg["data"])
            received += 1
            print(
                f"[결과 #{received}] "
                f"seg{result['segment_num']:03d} | "
                f"{result['start_pts']:.1f}s ~ {result['end_pts']:.1f}s\n"
                f"  → {result['text']}\n"
            )
    except Exception as e:
        print(f"[결과] 수신 중 오류: {e}")
    finally:
        pubsub.unsubscribe()
        print(f"[결과] 총 {received}개 수신 완료")


def main():
    sample_dir    = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "sample"
    )
    # whisper 컨테이너 안에서 sample이 마운트된 경로 (docker-compose: ./sample:/sample)
    container_dir = sys.argv[2] if len(sys.argv) > 2 else "/sample"

    sample_dir = os.path.abspath(sample_dir)
    print(f"[설정] sample_dir    : {sample_dir}")
    print(f"[설정] container_dir : {container_dir}")
    print(f"[설정] REDIS_URL     : {REDIS_URL}\n")

    stop_event = threading.Event()
    listener = threading.Thread(
        target=listen_results, args=(stop_event,), daemon=True
    )
    listener.start()
    time.sleep(0.3)
    # pubsub 구독 완료 대기

    try:
        push_segments(sample_dir, container_dir)
        # push 완료 후 결과가 다 올 때까지 대기
        # 마지막 세그먼트 처리 + Whisper 전사 시간 고려해 넉넉히 대기
        print("[대기] 마지막 결과 수신 대기 중... (Ctrl+C로 종료)")
        listener.join(timeout=120)
    except KeyboardInterrupt:
        print("\n[종료] 사용자 중단")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
