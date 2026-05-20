# 2026-PNU-CloudComputing-Team1

## 실행 방법

### 전체 서비스 실행

```bash
docker compose up -d
```

### 로그 확인

```bash
# 전체 로그
docker compose logs -f

# 특정 서비스 로그
docker compose logs -f whisper
docker compose logs -f redis
```

### 전체 서비스 중지

```bash
docker compose down
```

볼륨(모델 캐시, 오디오 데이터)까지 함께 삭제:

```bash
docker compose down -v
```