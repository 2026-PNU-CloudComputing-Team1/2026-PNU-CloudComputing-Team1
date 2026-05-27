# 2026-PNU-CloudComputing-Team1

클라우드 컴퓨팅 텀프로젝트: 전 세계 시청자를 위한 실시간 다국어 자막 생성 및 CDN 엣지 캐싱 기반 라이브 스트리밍 플랫폼

## 서비스 구성 요약

| 서비스 | 역할 |
| --- | --- |
| `mediamtx` | RTMP 수신 / HLS 변환 (영상 인제스트) |
| `live-transcoder` | FFmpeg — OBS 스트림 오디오 AAC 정규화 후 `live/smooth` 재발행 |
| `gcs-stt` | Google Cloud STT 스트리밍 — RTMP 오디오를 실시간 음성 인식 |
| `translator` | Google Cloud Translation — STT 결과를 영어·중국어·일본어로 번역 |
| `subtitle-pub` | VTT 세그먼트 생성 + HLS 자막 플레이리스트 관리 |
| `nginx-origin` | Origin 서버 — HLS 영상·자막 통합 제공 (포트 8080) |
| `edge-korea/japan/china/us` | 지역별 Nginx 엣지 — CDN 캐싱 시뮬레이션 |
| `backend` | FastAPI — WebSocket 자막 브로드캐스트 + 관리자 API |
| `redis` | Pub/Sub 파이프라인 + 메트릭 캐시 |
| `frontend` | React + hls.js — 영상 재생 + 실시간 자막 + 관리자 대시보드 |

## 실행 방법

### 전체 서비스 실행

```bash
docker compose up -d --build
```

> Google Cloud 서비스 계정 키(`secrets/gcp-key.json`)가 필요합니다.

### 로그 확인

```bash
# 전체 로그
docker compose logs -f

# 특정 서비스 로그
docker compose logs -f gcs-stt
docker compose logs -f translator
docker compose logs -f backend
docker compose logs -f mediamtx
```

### 전체 서비스 중지

```bash
docker compose down
```

볼륨(오디오·자막 데이터)까지 함께 삭제:

```bash
docker compose down -v
```

## 접속 주소

| 용도 | 주소 |
| --- | --- |
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/docs |
| RTMP ingest | `rtmp://localhost:1935/live` |
| Origin HLS | http://localhost:8080/hls/live/smooth/index.m3u8 |
| Origin 자막 | http://localhost:8080/master.m3u8 |
| 엣지 - 한국 | http://localhost:8081 |
| 엣지 - 일본 | http://localhost:8082 |
| 엣지 - 중국 | http://localhost:8083 |
| 엣지 - 미국 | http://localhost:8084 |

## 실시간 파이프라인 흐름

```text
OBS (카메라/마이크)
  → RTMP rtmp://localhost:1935/live/demo
  → MediaMTX (RTMP 수신)
  → live-transcoder (FFmpeg: 오디오 AAC 정규화)
  → RTMP live/smooth
  → MediaMTX (HLS 변환)
  → nginx-origin (/hls/live/smooth/index.m3u8)
  → 엣지 서버 (CDN 캐싱)
  → 브라우저 hls.js 재생

RTMP live/smooth (오디오 브랜치)
  → gcs-stt (FFmpeg 오디오 추출 → Google Cloud STT 스트리밍)
  → Redis Pub/Sub stt:results / stt:interim
  → translator (Google Cloud Translation: ko → en/zh/ja)
  → Redis Pub/Sub subtitle:translated / vtt:ready
  → subtitle-pub (VTT 파일 생성 + 플레이리스트 갱신)
  → nginx-origin (/subtitles/{lang}/playlist.m3u8)
  → backend (WebSocket 브로드캐스트 → 브라우저 자막 오버레이)
```

## 구현된 주요 기능

| 구분 | 구현 상태 |
| --- | --- |
| RTMP 수신 | MediaMTX가 `live/demo` 스트림 수신 |
| 영상 안정화 | live-transcoder가 오디오 AAC 44.1kHz/128k 정규화 |
| HLS 재생 | 브라우저에서 `live/smooth` HLS 스트림 재생 |
| 실시간 STT | Google Cloud STT 스트리밍 — 100ms 청크 단위 인식 |
| Interim 자막 | stability ≥ 0.8 + 어절 경계 감지 시 즉시 번역·표시 |
| Final 자막 | 문장 완성 시 정확도 높은 번역 결과 제공 |
| 다국어 번역 | Google Cloud Translation — 영어·중국어·일본어 병렬 번역 |
| HLS 자막 | WebVTT 세그먼트 + HLS 플레이리스트 (hls.js 통합) |
| CDN 엣지 | 지역별 Nginx 엣지 캐시 (한국 10ms / 일본 50ms / 중국 80ms / 미국 180ms 지연 시뮬레이션) |
| 엣지 페일오버 | 엣지 다운 시 프론트가 다른 엣지로 자동 전환 |
| WebSocket | FastAPI WebSocket으로 실시간 자막 프론트 전달 |
| 관리자 대시보드 | 지연 시간 메트릭 시각화 + 엣지 상태 제어 |
| Docker 실행 | 전체 서비스를 Compose 하나로 실행 |

## OBS 설정

OBS Studio에서 `Settings > Stream`으로 이동한다.

| 항목 | 값 |
| --- | --- |
| Service | Custom |
| Server | `rtmp://localhost:1935/live` |
| Stream Key | `demo` |

권장 인코딩 설정:

| 항목 | 값 |
| --- | --- |
| Output Resolution | `1280x720` |
| FPS | `30` |
| Video Bitrate | `2500 Kbps` |
| Encoder | Apple H.264 또는 x264 |
| Audio Bitrate | `128 Kbps` |

자세한 테스트 절차는 [docs/obs-live-test.md](docs/obs-live-test.md)를 참고한다.

## 프론트 사용 방법

1. `http://localhost:3000` 접속
2. `Playback Mode`에서 `Live Stream` 선택
3. OBS에서 `Start Streaming`
4. 웹 화면에서 `Play` 클릭
5. 마이크로 말하면 `Live Caption Log`에 실시간 자막이 표시됨
6. 언어 선택기로 한국어 원문 / 영어 / 중국어 / 일본어 전환 가능

## Redis Pub/Sub 채널 구조

| 채널 | 발행자 | 구독자 | 내용 |
| --- | --- | --- | --- |
| `stt:interim` | gcs-stt | backend, translator | 중간 인식 결과 (stability 포함) |
| `stt:results` | gcs-stt | translator | 최종 인식 결과 |
| `subtitle:translated` | translator | backend | 번역 완료 자막 (전 언어 묶음) |
| `subtitle:interim_translated` | translator | backend | 어절 경계 번역 결과 |
| `vtt:ready` | translator | subtitle-pub | VTT 파일 경로 + 지연 메트릭 |

## GCP 서비스 계정 설정

1. Google Cloud Console에서 서비스 계정 생성
2. 다음 역할 부여: `Cloud Speech-to-Text`, `Cloud Translation API`
3. JSON 키 파일을 `secrets/gcp-key.json`에 저장

## 현재 한계 및 다음 단계

- HLS 방식이므로 몇 초의 재생 지연이 발생할 수 있다.
- GCS STT 스트리밍은 5분 제한이 있으며 4분마다 자동 재연결한다.
- 실제 글로벌 배포 시 엣지 서버를 GCP/AWS 리전 VM 또는 CDN 서비스로 교체해야 한다.
