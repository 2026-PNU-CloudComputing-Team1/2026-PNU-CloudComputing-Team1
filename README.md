# 클라우드 기반 실시간 다국어 자막 스트리밍 플랫폼

라이브 방송에서 발생하는 음성을 실시간으로 인식하고, 자막으로 만들어 시청자 화면에 전달하는 클라우드 컴퓨팅 텀프로젝트입니다.

현재 구현은 로컬 Docker 환경에서 **OBS 라이브 송출 -> RTMP 수신 -> HLS 재생 -> 오디오 추출 -> Whisper 음성 인식 -> WebSocket 자막 출력**까지 연결한 상태입니다. 즉 단순히 미리 적어둔 자막을 보여주는 웹 페이지가 아니라, OBS에서 들어오는 실제 음성 스트림을 잘라 STT 파이프라인으로 넘기는 구조를 구현했습니다.

팀원 파트인 번역, WebVTT 퍼블리싱, CDN Edge 캐싱은 이후 연결 대상으로 남겨두고, 이 레포에서는 먼저 라이브 영상과 실시간 자막 파이프라인의 기반을 잡는 데 집중합니다.

## 구현된 핵심 흐름

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

## 현재 구현 범위

| 구분 | 구현 상태 |
| --- | --- |
| OBS 송출 | OBS에서 RTMP로 로컬 MediaMTX에 송출 가능 |
| RTMP 수신 | MediaMTX가 `live/demo` 스트림 수신 |
| HLS 재생 | 브라우저에서 `live/smooth` HLS 스트림 재생 |
| 영상 안정화 | FFmpeg `live-transcoder`로 30fps/1초 키프레임 보정 |
| 오디오 추출 | 백엔드 FFmpeg가 라이브 스트림에서 오디오를 1.5초 단위로 분리 |
| 음성 인식 | faster-whisper가 Redis 큐의 오디오 세그먼트를 STT 처리 |
| 자막 전달 | FastAPI WebSocket으로 프론트에 실시간 자막 push |
| 자막 UI | 영상 위 현재 자막 + 우측 Live Caption Log 표시 |
| Docker 실행 | Redis, MediaMTX, Backend, Whisper, Frontend를 Compose로 실행 |

## 실행 방법

```bash
docker compose up -d --build
```

접속 주소:

| 용도 | 주소 |
| --- | --- |
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/docs |
| RTMP ingest | `rtmp://localhost:1935/live` |
| HLS playback | http://localhost:8888/live/smooth/index.m3u8 |

종료:

```bash
docker compose down
```

## OBS 설정

OBS Studio에서 `Settings > Stream`으로 이동합니다.

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

자세한 테스트 절차는 [docs/obs-live-test.md](docs/obs-live-test.md)를 참고합니다.

## 프론트 사용 방법

1. `http://localhost:3000` 접속
2. `Playback Mode`에서 `Live Stream` 선택
3. OBS에서 `Start Streaming`
4. 웹 화면에서 `Play` 클릭
5. 마이크로 말하면 몇 초 뒤 `Live Caption Log`에 자막이 쌓이는지 확인

## 데모 모드와 라이브 모드 차이

| 모드 | 설명 |
| --- | --- |
| Demo Video | `sample/AWS.mp4`를 재생하면서 파일 기반 음성 인식 흐름을 확인 |
| Live Stream | OBS에서 들어오는 실제 RTMP/HLS 스트림의 음성을 인식 |

데모 모드는 테스트용이고, 프로젝트의 핵심 구현은 Live Stream 모드입니다.

## 현재 한계

- HLS는 Discord 화면 공유처럼 완전 초저지연/초고프레임 방식이 아닙니다. 몇 초 정도의 지연은 정상입니다.
- Whisper `base` 모델을 사용하므로 정확도가 완벽하지 않습니다.
- 로컬 노트북 CPU 환경에서는 STT 속도와 영상 부드러움 사이에 타협이 필요합니다.
- 번역, WebVTT 생성, S3/CloudFront 캐싱은 팀원 파트와 연결할 다음 단계입니다.

## 다음 단계

현재 큰 틀은 아래 수준까지 구체화되었습니다.

```text
라이브 송출
  -> 영상 전송 파이프라인
  -> 오디오 추출
  -> STT
  -> 브라우저 실시간 자막 출력
```

남은 연결 작업:

```text
STT 결과
  -> AWS Translate 또는 팀원 번역 모듈
  -> 언어별 WebVTT/JSON 생성
  -> Nginx/S3 Origin 저장
  -> CDN Edge 캐싱
  -> 언어 선택별 자막 제공
```

## 발표 포인트

- OBS 송출 영상을 RTMP로 수신하고 HLS로 변환하는 Origin 서버 구조
- FFmpeg를 이용한 라이브 오디오 세그먼트 추출
- Redis 큐를 통한 비동기 STT 처리
- WebSocket 기반 실시간 자막 전달
- 이후 언어별 자막 파일을 CDN Edge에 캐싱할 수 있는 확장 구조
