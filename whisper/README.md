# Whisper 서비스 인터페이스

## 입력

Redis List 큐, `stt:queue` 에 아래의 json 형식으로 `RPUSH`

```json
{
  "segment_num": 0,
  "audio_path":  "/data/audio/seg0000.wav",
  "pts":         0.0,
  "ingested_at": 1747123456.789
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `segment_num` | int | 세그먼트 순번 (0부터 시작) |
| `audio_path` | string | WAV 파일 절대 경로 (Docker volume `audio-data` → `/data/audio`), WAV 포맷: 16kHz 모노 |
| `pts` | float | 스트림 시작 기준 절대 시간(초). `seg_num * SEGMENT_DURATION`으로 계산 |
| `ingested_at` | float | 큐 삽입 시각 (지연 측정용) |


- WAV 길이가 `SEGMENT_DURATION`(기본 2초)과 ±0.1초 이상 차이나면 예외 발생

---

## 출력
Redis Pub/Sub 채널, `stt:results` 에 `PUBLISH`

```json
{
  "segment_num": 0,
  "text":        "안녕하세요 반갑습니다",
  "start_pts":   0.0,
  "end_pts":     2.0,
  "ingested_at": 1747123456.789
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `segment_num` | int | 입력과 동일한 순번 |
| `text` | string | 음성 인식 결과 텍스트 |
| `start_pts` | float | 자막 시작 시각(초) = 입력의 `pts` |
| `end_pts` | float | 자막 종료 시각(초) = `start_pts + SEGMENT_DURATION` |
| `ingested_at` | float | 입력의 `ingested_at` |

