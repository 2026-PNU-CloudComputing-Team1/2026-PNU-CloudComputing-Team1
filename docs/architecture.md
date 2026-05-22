# 아키텍처 문서

## 1. MVP 아키텍처

```text
React Viewer
  - video player
  - subtitle language selector
  - recent subtitle history
        |
        | REST + WebSocket
        v
FastAPI Backend
  - stream metadata API
  - subtitle WebSocket
  - mock STT pipeline
  - mock translation pipeline
        |
        v
Redis
  - recent subtitle cache
  - stream state cache
```

## 2. 데이터 흐름

1. 시청자가 React 페이지에 접속합니다.
2. React 앱이 FastAPI의 `/streams/{stream_id}`와 `/health`를 호출합니다.
3. React 앱이 `/ws/stream/{stream_id}` WebSocket에 연결합니다.
4. FastAPI는 스트림별 자막 생성 loop를 시작합니다.
5. 2초마다 mock STT 문장이 생성됩니다.
6. `TranslationService`가 영어, 일본어, 중국어 자막을 생성합니다.
7. 자막은 Redis에 최근 기록으로 저장됩니다.
8. FastAPI가 WebSocket으로 모든 시청자에게 자막을 전송합니다.
9. 시청자는 원하는 언어를 선택해 자막을 확인합니다.

## 3. 클라우드 확장 아키텍처

```text
Streamer OBS / Browser
        |
        v
Amazon IVS or MediaLive
        |
        +------------------> HLS video segments
        |                         |
        |                         v
        |                    S3 / CloudFront
        |
        v
Audio Extractor
        |
        v
Speech-to-Text
        |
        v
Translation API
        |
        v
WebVTT Segment Generator
        |
        v
S3 subtitle/{stream}/{lang}/segment.vtt
        |
        v
CloudFront Edge Cache
        |
        v
Global Viewers
```

## 4. 사용 가능한 AWS 매핑

| 현재 MVP | AWS 확장 |
| --- | --- |
| Local FastAPI | ECS, EC2, Elastic Beanstalk, Lambda |
| Local Redis | ElastiCache for Redis |
| Mock STT | Amazon Transcribe Streaming |
| Mock Translation | Amazon Translate |
| Local subtitle cache | S3 subtitle segment storage |
| Browser delivery | CloudFront CDN |
| Docker Compose | ECS/Fargate |

## 5. 설계상 한계

- 현재 MVP는 실제 마이크 음성 인식을 수행하지 않고 mock 문장을 사용합니다.
- WebSocket은 실시간 전송에는 적합하지만 CDN 캐싱과 직접적으로 잘 맞지 않습니다.
- CDN 캐싱을 강조하려면 WebVTT 자막 세그먼트를 S3에 저장하고 CloudFront로 전달하는 방식이 적합합니다.
- 실제 대규모 서비스에서는 영상 트래픽 비용과 지연 시간 최적화가 가장 중요합니다.
