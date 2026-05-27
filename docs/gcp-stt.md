# GCP STT 파이프라인

OBS 송출 음성을 Google Cloud Speech-to-Text v1으로 인식하고, 어절 경계에서 실시간으로 번역해 시청자에게 다국어 자막을 제공하는 파이프라인 문서.

## 1. 전체 흐름

```text
OBS RTMP push
        │
        ▼
  mediamtx (live/demo)
        │
        ▼
  live-transcoder (ffmpeg)
        │ 영상 copy, 오디오 AAC 정규화
        ▼
  mediamtx (live/smooth)
        │
        ├─────────────────────────────────────────────────┐
        │                                                 │
        ▼                                                 ▼
  hls.js (브라우저 영상 재생)                  gcs-stt 컨테이너
                                                          │ ffmpeg → 16kHz mono PCM
                                                          ▼
                                          google-cloud-speech v1
                                          streaming_recognize
                                                          │
                                          ┌───────────────┴───────────────┐
                                          ▼                               ▼
                                    is_final=False                  is_final=True
                                          │                               │
                                          ▼                               ▼
                                  Redis stt:interim              Redis stt:results
                                          │                               │
                                          ▼                               ▼
                                      translator                      translator
                                  (어절+stability)                (Google Translate)
                                          │                               │
                                          ▼                               ▼
                          Redis subtitle:interim_translated   Redis subtitle:translated
                                          │                               │
                                          ▼                               ▼
                                      backend                          backend
                                          │                               │
                                          ▼                               ▼
                          WS subtitle_interim_translated         WS subtitle
                                          │                               │
                                          └───────────────┬───────────────┘
                                                          ▼
                                              React StreamPlayer
                                              ├─ 오버레이 (회색 interim)
                                              └─ Live Caption Log (final)
```

## 2. 컴포넌트별 역할

### 2.1 gcs-stt 컨테이너

`gcs-stt/app.py` — RTMP에서 음성을 추출해 GCS streaming API에 흘려보내고, 응답을 Redis로 publish.

- **입력**: `rtmp://mediamtx:1935/live/smooth` (live-transcoder의 출력)
- **오디오 추출**: ffmpeg `-vn -ac 1 -ar 16000 -f s16le pipe:1`
- **인식**: `speech.StreamingRecognitionConfig(language_code="ko-KR", interim_results=True, enable_automatic_punctuation=True)`
- **응답 처리**:
  - GCS 응답의 `results[0]`만 사용 (`results[1+]`는 불안정한 단편 후보)
  - `is_final=True` → `stt:results`에 publish, `seg_counter` +1
  - `is_final=False` → `stt:interim`에 publish, `stability` 함께 송출
- **재연결**: v1은 streaming 5분 제한 → `MAX_STREAM_SEC=240`마다 재연결

### 2.2 translator 컨테이너

`translator/app.py` — Redis pub/sub로 STT 결과를 받아 Google Cloud Translation으로 영/일/중 번역.

두 채널을 동시에 구독:

| 입력 채널 | 처리 함수 | 출력 채널 |
|---|---|---|
| `stt:results` (final) | `handle_stt_result()` | `subtitle:translated`, `vtt:ready` |
| `stt:interim` | `handle_interim()` | `subtitle:interim_translated` |

interim 번역 조건:
1. `stability >= INTERIM_STABILITY_MIN` (기본 0.8)
2. 텍스트에 공백이 1개 이상 (= 최소 한 어절 완료)
3. 마지막 공백 이전 텍스트가 직전 번역과 다름

→ 어절 경계(공백) 직전까지만 번역. 마지막 어절(아직 진행 중인 단어)은 제외.

state 관리:
- `_interim_last: dict[str, str]` — stream_id별 마지막 번역 텍스트
- final 도착 시 `pop(stream_id)`로 비움 → 다음 발화에서 깔끔히 재시작
- 현재 interim이 직전 번역의 확장이 아니면 새 발화로 간주

### 2.3 backend (FastAPI)

`backend/app/main.py` — Redis 채널을 WebSocket으로 중계.

| Redis 채널 | listener 함수 | WS 메시지 타입 |
|---|---|---|
| `subtitle:translated` | `translated_subtitle_listener` | `subtitle` (final + translations) |
| `stt:interim` | `interim_subtitle_listener` | `subtitle_interim` (한국어 원문) |
| `subtitle:interim_translated` | `translated_interim_listener` | `subtitle_interim_translated` (번역된 interim) |

startup 시점에 세 task가 `asyncio.create_task`로 떠서 평생 돌고, shutdown 시 cancel.

브로드캐스트 대상은 `STT_STREAM_ID` env로 고정 (기본 `demo`).

### 2.4 frontend (React)

`frontend/src/components/StreamPlayer.jsx` — WebSocket을 통해 받은 메시지로 자막 표시.

state:
- `interimText: string` — 한국어 GCS interim 원문
- `interimTranslations: {[lang]: string}` — translator가 어절 단위로 번역한 결과
- `currentSubtitle: SubtitleMessage | null` — 가장 최근 final
- `subtitles: SubtitleMessage[]` — 최근 30개 final (시각 오름차순)

오버레이 렌더링 규칙 (영상 좌측 하단):

```js
const overlayText = language === 'original'
  ? interimText
  : (interimTranslations[language] || '');

if (overlayText) → 회색 interim 표시 (최대 2줄, 80자 넘으면 …+꼬리)
else if (!currentSubtitle) → "실시간 자막을 기다리는 중입니다." placeholder
else → 빈 화면
```

우측 Live Caption Log: `subtitles` 배열을 시각 + 선택 언어 텍스트로 시간순 표시.

## 3. Redis 채널 명세

| 채널 | producer | consumer | 페이로드 |
|---|---|---|---|
| `stt:interim` | gcs-stt | translator, backend | `{text, stream_id, stability}` |
| `stt:results` | gcs-stt | translator | `{segment_num, text, start_pts, end_pts, ingested_at, stream_id}` |
| `subtitle:interim_translated` | translator | backend | `{stream_id, original_text, translations, stability}` |
| `subtitle:translated` | translator | backend | `{segment_num, original_text, translations, start_pts, end_pts, ingested_at, buffer_wait, stt_delay, translation_delay, subtitle_delay}` |
| `vtt:ready` | translator | subtitle-pub | `{segment_num, lang, vtt_path, subtitle_delay}` |

## 4. WebSocket 메시지 명세

| `type` | 트리거 | `data` |
|---|---|---|
| `subtitle_interim` | gcs-stt interim 도착 | `{text}` (한국어 원문) |
| `subtitle_interim_translated` | translator가 어절 경계 번역 완료 | `{original_text, translations: {en, ja, zh}}` |
| `subtitle` | translator가 final 번역 완료 | `SubtitleMessage` (id, timestamp, duration, original_text, translations) |
| `subtitle_reset` | caption-demo 재시작 | (data 없음) |

## 5. 환경 변수

### gcs-stt
- `REDIS_URL` (기본 `redis://localhost:6379`)
- `RTMP_URL` (기본 `rtmp://mediamtx:1935/live/smooth`)
- `SOURCE_LANG` (기본 `ko-KR`)
- `STREAM_ID` (기본 `demo`)

### translator
- `REDIS_URL`
- `SOURCE_LANG` (기본 `ko`)
- `TARGET_LANGS` (기본 `en,zh,ja`)
- `VTT_DIR` (기본 `/data/subtitles`)
- `INTERIM_STABILITY_MIN` (기본 `0.8`) — interim 번역 임계값
- `GOOGLE_APPLICATION_CREDENTIALS` — 서비스 계정 JSON 경로

### backend
- `REDIS_URL`
- `STT_STREAM_ID` (기본 `demo`) — WS 브로드캐스트 대상 stream
- `SUBTITLE_TRANSLATED_CHANNEL` (기본 `subtitle:translated`)

## 6. 시청자 모드별 동작 매트릭스

| 시청자 언어 | 발화 중 (final 전) | final 도착 후 |
|---|---|---|
| 한국어 (`original`) | GCS interim 원문 실시간 표시 (회색) | 오버레이 빈 화면, Caption Log에 한국어 누적 |
| 영어/일본어/중국어 | 어절 끝날 때마다 그때까지 번역해 회색으로 표시 (stability ≥ 0.8 조건) | 오버레이 빈 화면, Caption Log에 번역 final 누적 |

발화 예시 (영어 모드):

```
사용자: "안녕하세요 산업용 노트북에 대해 설명드리겠습니다"

interim:  "안녕"                              → stability 낮음 또는 공백 없음, skip
          "안녕하세요"                          → 공백 없음, skip
          "안녕하세요 산업용"                   → 어절 끝 "안녕하세요" 번역 → "Hello"
          "안녕하세요 산업용 노트북에"          → "안녕하세요 산업용" 번역 → "Hello industrial"
          ... 계속 ...
final:    "안녕하세요 산업용 노트북에 대해 설명드리겠습니다"
                                            → Caption Log에 "Hello, let me explain about industrial laptops"
```

## 7. 알려진 한계

### 7.1 GCS v1 streaming의 final 지연

v1은 침묵 감지에만 의존해 `is_final`을 내려보냄. 사용자가 끊김 없이 길게 말하면 final이 수십 초 늦게 올 수 있음. interim 번역 기능이 이를 일부 보완하지만, **Caption Log에 final이 쌓이는 속도는 여전히 발화자의 호흡 패턴에 의존**.

대안: GCS Speech v2의 `voice_activity_events`로 마이그레이션하면 짧은 침묵에도 utterance를 끊을 수 있음.

### 7.2 interim 번역 깜빡임

한국어는 SOV 구조라 동사 어미가 와야 의미가 확정됨. 미완성 interim을 번역하면 어순/시제가 갈아엎힐 수 있음:

- "산업용 노트북에 대해" → "About industrial laptops"
- 한 어절 후 "산업용 노트북에 대해 설명드리겠" → "Let me explain about industrial laptops"

stability 0.8 임계값으로 일부 완화되지만 완전 제거는 어려움.

### 7.3 4분 재연결 시 PTS 리셋

`MAX_STREAM_SEC=240` 도달로 GCS streaming을 재연결하면 `stream_start`가 0으로 리셋됨 ([gcs-stt/app.py](../gcs-stt/app.py)의 `elapsed = now - stream_start`). 5분 이상 방송 시 Caption Log의 시각 정렬이 깨질 수 있음.

### 7.4 단일 stream 가정

translator의 state(`_interim_last`)와 backend의 broadcast(`STT_STREAM_ID`)가 모두 단일 stream 전제로 짜여 있음. 멀티 stream 지원하려면 stream_id 라우팅 일반화 필요.

## 8. 디버깅 가이드

### 8.1 컨테이너 로그

```bash
docker compose logs -f gcs-stt    | grep -E "seg|interim"
docker compose logs -f translator | grep -E "interim 번역|seg"
docker compose logs -f backend    | grep -E "Broadcast|listener"
```

기대 로그 예시:

```
gcs-stt    | [gcs-stt] seg0003 완료 | '안녕하세요 산업용 노트북'
translator | [translator] interim 번역 stability=0.85 | '안녕하세요 산업용'
translator | [translator] seg0003 en 완료 | 'Hello industrial notebook'
backend    | Broadcast translated subtitle stream=demo segment=3 langs=['en', 'ja', 'zh']
```

### 8.2 브라우저 Console

DevTools Console에 다음 로그가 찍힘:

- `[stt:interim] <text>` — interim 갱신 (시끄러움)
- `[stt:interim_translated] {original_text, translations}` — 어절 번역 도착
- `[stt:final] {id, timestamp, original, translations}` — final 도착
- `[stt:reset]` — caption-demo 재시작

조용히 보려면 콘솔 필터에 `stt:final` 또는 `stt:interim_translated`만 입력.

### 8.3 interim 번역이 안 보일 때

가능한 원인:

1. **stability가 임계값 못 넘김** — translator 로그에 `interim 번역` 라인이 없음. `INTERIM_STABILITY_MIN`을 0.6~0.7로 낮춰서 시도.
2. **공백이 없는 발화** — 한 어절짜리 짧은 발화는 번역 안 됨 (마지막 공백 기준이므로). 디자인상 의도.
3. **translator 다운 또는 Translate API 권한 문제** — `docker compose logs translator`에서 에러 확인.
4. **frontend WS 미수신** — Console에서 `[stt:interim_translated]` 로그 없음. backend `translated_interim_listener` 살아있는지 확인.

## 9. 관련 파일

- [gcs-stt/app.py](../gcs-stt/app.py) — STT 진입점
- [translator/app.py](../translator/app.py) — 번역 디스패처
- [backend/app/main.py](../backend/app/main.py) — WS 중계
- [frontend/src/components/StreamPlayer.jsx](../frontend/src/components/StreamPlayer.jsx) — 자막 UI
- [docker-compose.yml](../docker-compose.yml) — 서비스 정의
