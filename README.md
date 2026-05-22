# 2026-PNU-CloudComputing-Team1

클라우드 컴퓨팅 텀프로젝트: 전 세계 시청자를 위한 실시간 다국어 자막 생성 및 CDN 엣지 캐싱 기반 라이브 스트리밍 플랫폼

## 실행 방법

### 전체 서비스 실행

```bash
docker compose up -d --build
```

### 로그 확인

```bash
# 전체 로그
docker compose logs -f

# 특정 서비스 로그
docker compose logs -f whisper
docker compose logs -f redis
docker compose logs -f backend
docker compose logs -f mediamtx
```

### 전체 서비스 중지

```bash
docker compose down
```

볼륨(모델 캐시, 오디오 데이터)까지 함께 삭제:

```bash
docker compose down -v
```

## 현재 추가 구현 내용

기존 Whisper 기반 음성 인식 파트에 더해, OBS 라이브 송출부터 브라우저 자막 출력까지 이어지는 실시간 스트리밍 파이프라인을 추가하였다.

현재 구현된 흐름은 다음과 같다.

```text
OBS Camera/Mic
  -> RTMP: rtmp://localhost:1935/live/demo
  -> MediaMTX RTMP ingest
  -> FFmpeg live-transcoder
  -> RTMP: live/smooth
  -> MediaMTX HLS
  -> React + hls.js video player

RTMP live/smooth
  -> Backend FFmpeg audio extraction
  -> 1.5s WAV segment
  -> Redis STT queue
  -> faster-whisper
  -> FastAPI WebSocket
  -> Browser live caption log
```

## 구현된 주요 기능

| 구분 | 구현 상태 |
| --- | --- |
| OBS 송출 | OBS에서 RTMP로 로컬 MediaMTX에 송출 가능 |
| RTMP 수신 | MediaMTX가 `live/demo` 스트림 수신 |
| HLS 재생 | 브라우저에서 `live/smooth` HLS 스트림 재생 |
| 영상 안정화 | FFmpeg `live-transcoder`로 30fps/1초 키프레임 보정 |
| 오디오 추출 | 백엔드 FFmpeg가 라이브 스트림에서 오디오를 1.5초 단위로 분리 |
| 음성 인식 | faster-whisper가 Redis 큐의 오디오 세그먼트를 STT 처리 |
| 자막 전달 | FastAPI WebSocket으로 프론트에 실시간 자막 전달 |
| 자막 UI | 영상 위 현재 자막 + 우측 Live Caption Log 표시 |
| Docker 실행 | Redis, MediaMTX, Backend, Whisper, Frontend를 Compose로 실행 |

## 접속 주소

| 용도 | 주소 |
| --- | --- |
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/docs |
| RTMP ingest | `rtmp://localhost:1935/live` |
| HLS playback | http://localhost:8888/live/smooth/index.m3u8 |

## OBS 설정

OBS Studio에서 `Settings > Stream`으로 이동한다.

| 항목 | 값 |
| --- | --- |
| Service | Custom |
| Server | `rtmp://localhost:1935/live` |
| Stream Key | `demo` |

권장 설정:

| 항목 | 값 |
| --- | --- |
| Output Resolution | `1280x720` |
| FPS | `30` |
| Video Bitrate | `2500 Kbps` |
| Encoder | Apple H.264 또는 x264 |

자세한 테스트 절차는 [docs/obs-live-test.md](docs/obs-live-test.md)를 참고한다.

## 프론트 사용 방법

1. `http://localhost:3000` 접속
2. `Playback Mode`에서 `Live Stream` 선택
3. OBS에서 `Start Streaming`
4. 웹 화면에서 `Play` 클릭
5. 마이크로 말하면 몇 초 뒤 `Live Caption Log`에 자막이 쌓이는지 확인

## 데모 모드와 라이브 모드

| 모드 | 설명 |
| --- | --- |
| Demo Video | `sample/AWS.mp4`를 재생하면서 파일 기반 음성 인식 흐름 확인 |
| Live Stream | OBS에서 들어오는 실제 RTMP/HLS 스트림의 음성을 인식 |

## 현재 한계 및 다음 단계

- HLS 방식이므로 Discord 화면 공유처럼 완전 초저지연 재생은 어렵고, 몇 초 정도의 지연이 발생할 수 있다.
- Whisper `base` 모델을 사용하므로 음성 인식 정확도는 추후 개선이 필요하다.
- 다음 단계에서는 STT 결과를 번역 모듈, WebVTT/JSON 자막 생성, S3/CloudFront 또는 Nginx Edge 캐싱 구조와 연결한다.

```text
STT 결과
  -> AWS Translate 또는 팀원 번역 모듈
  -> 언어별 WebVTT/JSON 생성
  -> Nginx/S3 Origin 저장
  -> CDN Edge 캐싱
  -> 언어 선택별 자막 제공
```
