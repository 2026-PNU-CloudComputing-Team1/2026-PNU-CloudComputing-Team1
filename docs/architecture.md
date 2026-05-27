# 아키텍처 문서

## 1. 전체 시스템 아키텍처

```text
┌─────────────────────────────────────────────────────────────────┐
│  스트리머                                                        │
│  OBS (카메라/마이크)                                             │
└───────────────────┬─────────────────────────────────────────────┘
                    │ RTMP rtmp://localhost:1935/live/demo
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  MediaMTX                                                        │
│  RTMP 인제스트 + HLS 변환                                        │
└──────────┬──────────────────────────────────────────────────────┘
           │ RTMP live/smooth (live-transcoder 정규화 후)
           ▼
┌──────────────────────┐      ┌──────────────────────────────────┐
│  live-transcoder      │      │  gcs-stt                         │
│  FFmpeg 오디오 정규화 │      │  FFmpeg 오디오 추출              │
│  AAC 44.1kHz/128k    │      │  → Google Cloud STT 스트리밍     │
└──────────────────────┘      │  → Redis Pub/Sub stt:results      │
                               │              stt:interim          │
                               └──────────────┬───────────────────┘
                                              │
                                              ▼
                               ┌──────────────────────────────────┐
                               │  translator                        │
                               │  Google Cloud Translation          │
                               │  ko → en / zh / ja 병렬 번역      │
                               │  → Redis subtitle:translated       │
                               │  → Redis vtt:ready                 │
                               └──────────┬───────────────────────┘
                                          │
                    ┌─────────────────────┴──────────────────┐
                    ▼                                         ▼
     ┌──────────────────────────┐          ┌───────────────────────────┐
     │  subtitle-pub             │          │  backend (FastAPI)         │
     │  VTT 세그먼트 생성        │          │  WebSocket 브로드캐스트    │
     │  HLS 자막 플레이리스트    │          │  지연 메트릭 수집          │
     └───────────┬──────────────┘          └──────────┬────────────────┘
                 │                                     │ WebSocket
                 ▼                                     │
     ┌──────────────────────────┐                      │
     │  nginx-origin (포트 8080) │                      │
     │  /hls/       → MediaMTX  │                      │
     │  /subtitles/ → VTT 파일  │                      │
     │  /master.m3u8            │                      │
     └───────────┬──────────────┘                      │
                 │                                     │
     ┌───────────┴──────────────────────────┐          │
     │  엣지 서버 (CDN 캐싱 시뮬레이션)     │          │
     │  edge-korea  :8081  (지연 10ms)      │          │
     │  edge-japan  :8082  (지연 50ms)      │          │
     │  edge-china  :8083  (지연 80ms)      │          │
     │  edge-us     :8084  (지연 180ms)     │          │
     └───────────┬──────────────────────────┘          │
                 │                                     │
                 ▼                                     ▼
     ┌─────────────────────────────────────────────────────────────┐
     │  Frontend (React + hls.js)                                   │
     │  - 지역별 엣지 라우팅 (위치 기반 자동 선택 + 페일오버)       │
     │  - HLS 영상 재생 + WebVTT 자막 트랙                          │
     │  - WebSocket 실시간 자막 오버레이                             │
     │  - 관리자 대시보드 (지연 메트릭 + 엣지 상태 제어)            │
     └─────────────────────────────────────────────────────────────┘
```

## 2. 데이터 흐름 — 자막 파이프라인

### 2-1. Final 자막 경로 (정확도 우선)

1. gcs-stt가 RTMP 오디오를 100ms 청크로 Google Cloud STT에 스트리밍
2. 문장 완성(`is_final=true`) 시 `stt:results` 채널에 publish
3. translator가 `stt:results` 구독 → Google Cloud Translation으로 en/zh/ja 병렬 번역
4. 번역 완료 후 두 곳에 publish:
   - `subtitle:translated` → backend가 구독해 WebSocket으로 브로드캐스트
   - `vtt:ready` → subtitle-pub이 구독해 VTT 파일 + 플레이리스트 갱신
5. 브라우저: WebSocket 자막 오버레이 + hls.js WebVTT 트랙

### 2-2. Interim 자막 경로 (실시간성 우선)

1. gcs-stt가 인식 중인 텍스트를 `stt:interim` 채널에 지속 publish (stability 포함)
2. translator: `stability ≥ 0.8` + 어절 경계 감지 시 즉시 번역
3. `subtitle:interim_translated` → backend → WebSocket으로 브라우저에 전달
4. Final 도착 시 interim 상태 초기화

## 3. Redis Pub/Sub 채널 구조

```text
gcs-stt ──PUBLISH──► stt:interim          ─► backend  (중간 인식 오버레이)
                                           ─► translator (어절 경계 번역)

gcs-stt ──PUBLISH──► stt:results          ─► translator (최종 번역)

translator ─PUBLISH─► subtitle:translated  ─► backend  (WebSocket 브로드캐스트)
translator ─PUBLISH─► subtitle:interim_translated ─► backend (interim 번역 브로드캐스트)
translator ─PUBLISH─► vtt:ready            ─► subtitle-pub (VTT 파일 + 플레이리스트)
```

## 4. CDN 엣지 캐싱 전략

| 파일 유형 | nginx-origin Cache-Control | 엣지 동작 |
| --- | --- | --- |
| `.m3u8` | `no-cache, no-store` | 항상 origin 통과 (라이브 갱신) |
| `.vtt` | `public, max-age=3600` | 엣지에서 최대 1시간 캐시 |
| `.ts` | `public, max-age=3600` | 엣지에서 최대 1시간 캐시 |

엣지는 응답 헤더에 `X-Cache-Status: HIT/MISS`와 `X-Edge-Delay-Ms`를 노출해 브라우저 DevTools에서 캐싱 동작을 확인할 수 있다.

## 5. 엣지 페일오버

프론트엔드(`edgeRouting.js`)가 매 N초마다 각 엣지의 `/health` 엔드포인트를 폴링해 응답이 없으면 우선순위에 따라 다른 엣지로 자동 전환한다. 관리자 대시보드에서 특정 엣지를 수동으로 Kill/Restart해 페일오버 동작을 시연할 수 있다.

## 6. 서비스별 기술 스택

| 서비스 | 기술 |
| --- | --- |
| `gcs-stt` | Python, FFmpeg, Google Cloud Speech-to-Text (Streaming) |
| `translator` | Python asyncio, Google Cloud Translation v2 |
| `subtitle-pub` | Python asyncio, WebVTT, HLS |
| `backend` | Python, FastAPI, Redis Pub/Sub, WebSocket, Docker SDK |
| `nginx-origin` | Nginx (reverse proxy + 정적 파일 서빙) |
| `edge-*` | Nginx (proxy cache + `tc` 네트워크 지연 시뮬레이션) |
| `frontend` | React, hls.js, Vite |
| `mediamtx` | MediaMTX (RTMP → HLS) |
| 공통 | Docker Compose, Redis 7 |

## 7. GCS STT 스트리밍 제한 대응

Google Cloud STT Streaming API는 단일 세션 최대 5분 제한이 있다. gcs-stt는 4분(`MAX_STREAM_SEC=240`)마다 자동 재연결하며 segment 카운터를 이어받아 자막 연속성을 유지한다.

## 8. 클라우드 확장 매핑

| 현재 로컬 구성 | 클라우드 대응 |
| --- | --- |
| MediaMTX (로컬 RTMP) | AWS MediaLive / GCP Live Stream API |
| nginx-origin | S3 + CloudFront Origin |
| edge-* (로컬 Nginx) | CloudFront Edge / GCP CDN Edge |
| gcs-stt | Google Cloud STT (현재 사용 중) |
| translator | Google Cloud Translation (현재 사용 중) |
| Redis (로컬) | GCP Memorystore / AWS ElastiCache |
| Backend (로컬 FastAPI) | GCP Cloud Run / AWS Fargate |
| Docker Compose | GKE / ECS |
