# OBS Live Stream Test

이 문서는 로컬 Docker 환경에서 OBS 송출을 MediaMTX로 받고, 브라우저에서 HLS로 재생하는 테스트 절차이다.
현재 담당 구현 범위는 OBS 라이브 송출, RTMP/HLS 영상 파이프라인, FFmpeg 오디오 추출, 브라우저 플레이어, 실시간 자막 로그까지이다.

## 1. Docker 실행

```bash
docker compose up -d --build
```

실행 후 주요 포트:

| 용도 | 주소 |
| --- | --- |
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| RTMP ingest | rtmp://localhost:1935/live |
| HLS playback | http://localhost:8888/live/smooth/index.m3u8 |

## 2. OBS 설정

OBS Studio에서 `Settings > Stream`으로 이동한다.

| 항목 | 값 |
| --- | --- |
| Service | Custom |
| Server | `rtmp://localhost:1935/live` |
| Stream Key | `demo` |

설정 후 `Start Streaming`을 누르면 OBS 화면이 MediaMTX로 송출된다.

주의: 샘플 MP4를 RTMP로 자동 송출하는 `stream-publisher`는 OBS 없이 HLS 테스트할 때만 사용한다.
OBS를 사용할 때는 같은 stream key인 `demo`를 동시에 사용할 수 없으므로 `stream-publisher`를 끄고 테스트한다.

샘플 publisher가 켜져 있다면 다음 명령으로 끈다.

```bash
docker compose stop stream-publisher
```

## 3. 브라우저 확인

1. `http://localhost:3000` 접속
2. `Playback Mode`에서 `Live Stream` 선택
3. `Play` 클릭

이때 영상 경로는 다음과 같이 연결된다.

```text
OBS
→ RTMP: rtmp://localhost:1935/live/demo
→ MediaMTX
→ FFmpeg live-transcoder: live/demo → live/smooth
→ HLS: http://localhost:8888/live/smooth/index.m3u8
→ React + hls.js player
```

`live-transcoder`는 OBS에서 키프레임 간격을 직접 조정하기 어려운 경우를 위해 들어간 보정 단계이다.
OBS 입력을 30fps, 1초 키프레임, 2500Kbps로 다시 인코딩해서 HLS가 더 부드럽게 재생되도록 만든다.

## 4. OBS 권장 설정

HLS는 키프레임 기준으로 세그먼트를 만들기 때문에 OBS 키프레임 간격이 너무 길면 브라우저에서 첫 화면만 보이거나 재생이 멈춘 것처럼 보일 수 있다.

`Settings > Output > Streaming`에서 다음 값을 권장한다.

| 항목 | 값 |
| --- | --- |
| Video Bitrate | `2500 Kbps` |
| Keyframe Interval | `2 s` |
| Encoder | Apple H.264 또는 x264 |

`Settings > Video`에서는 다음 값을 권장한다.

| 항목 | 값 |
| --- | --- |
| Base Resolution | `1280x720` |
| Output Resolution | `1280x720` |
| FPS | `30` |

현재 테스트는 로컬 HLS 재생과 STT 확인이 목적이므로 1080p 60fps보다 720p 30fps가 안정적이다.

## 5. Live STT 확인

`Live Stream` 모드에서 `Play`를 누르면 백엔드는 RTMP 입력을 FFmpeg로 읽어서 1.5초 단위 WAV 세그먼트를 생성한다.
Whisper는 세그먼트를 약 3초 버퍼로 묶어 음성을 인식하고, 결과를 WebSocket으로 브라우저에 전달한다.

```text
RTMP live audio
→ FFmpeg audio segment
→ Redis queue
→ faster-whisper STT
→ Backend WebSocket
→ Live Caption Log
```

## 6. 문제 확인 명령어

HLS 주소가 열리는지 확인:

```bash
curl http://localhost:8888/live/smooth/index.m3u8
```

컨테이너 상태 확인:

```bash
docker compose ps
```

OBS 없이 샘플 MP4를 RTMP 라이브처럼 테스트하고 싶을 때:

```bash
docker compose --profile sample-live up -d stream-publisher
```

백엔드/Whisper 로그 확인:

```bash
docker compose logs -f backend whisper mediamtx
```

## 7. 참고

현재 데모 영상 모드는 샘플 MP4 파일을 라이브처럼 2초 단위로 처리하는 시뮬레이션이다.
Live Stream 모드는 실제 OBS RTMP 입력을 받아 HLS 재생과 STT 파이프라인을 연결하는 구조이다.

## 8. 현재 구현된 담당 범위

| 항목 | 상태 |
| --- | --- |
| OBS 송출 | `rtmp://localhost:1935/live/demo`로 송출 가능 |
| RTMP 수신 | MediaMTX가 OBS 스트림 수신 |
| HLS 변환 | `live/smooth` 경로로 브라우저 재생 |
| 영상 보정 | `live-transcoder`가 30fps/1초 키프레임으로 재인코딩 |
| 오디오 추출 | Backend FFmpeg가 라이브 스트림을 1.5초 단위 WAV로 분리 |
| STT 연결 | Redis 큐를 통해 faster-whisper로 전달 |
| 자막 표시 | WebSocket으로 현재 자막과 Live Caption Log 표시 |

팀원 파트와 연결할 다음 단계는 STT 결과를 번역 모듈, WebVTT 생성, S3/CloudFront 또는 Nginx Edge 캐싱으로 넘기는 것이다.
