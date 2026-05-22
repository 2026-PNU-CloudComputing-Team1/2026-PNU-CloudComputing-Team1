# 비용 분석

아래 비용 분석은 발표용 추정치입니다. 실제 비용은 리전, 트래픽, 해상도, API 사용량에 따라 달라집니다.

## 1. 비용 구성 요소

| 항목 | 기준 | 설명 |
| --- | --- | --- |
| Compute | 서버 실행 시간 | FastAPI, 자막 처리 worker |
| Cache | Redis 사용량 | 최근 자막, 스트림 상태 |
| STT | 방송 시간 | 라이브 음성 인식 |
| Translation | 문자 수 x 언어 수 | 자막 번역 |
| Storage | 자막/영상 세그먼트 저장량 | S3 또는 Cloud Storage |
| CDN | 전송량 | 영상과 자막을 전세계 시청자에게 전달 |

## 2. 과제 MVP 비용

로컬 Docker Compose로 실행하면 비용은 0원입니다.

```text
React + FastAPI + Redis = local machine
Mock STT/Translation = $0
Cloud deployment = optional
```

## 3. 클라우드 배포 예시

작은 데모 기준:

| 서비스 | 예상 비용 |
| --- | --- |
| EC2 또는 Cloud Run | 무료 티어 또는 소액 |
| Redis | 로컬/프리티어 사용 시 0원 |
| S3 자막 저장 | 1GB 미만이면 매우 적음 |
| STT 1시간 | 대략 $1~$2 수준 |
| Translation 25만 문자 | 대략 $4~$5 수준 |
| CDN 10~100GB | 트래픽에 따라 증가 |

## 4. 1시간 방송 예시

가정:

- 방송 시간: 1시간
- 번역 언어: 영어, 일본어, 중국어 3개
- 원문 자막: 약 50,000자
- 시청자: 100명
- 영상: 720p, 약 2.5Mbps

AI 비용:

```text
STT: 60분 x 분당 과금
Translation: 50,000자 x 3개 언어 = 150,000자
```

트래픽:

```text
시청자 1명 1시간 720p 시청 = 약 1.1GB
100명 = 약 110GB
```

결론적으로 대규모 서비스에서는 STT/번역보다 영상 CDN 전송 비용이 더 큰 비중을 차지할 수 있습니다.

## 5. 비용 최적화 전략

- 자막은 텍스트이므로 영상보다 훨씬 작아 CDN 캐싱 효율이 좋습니다.
- 같은 문장 번역 결과는 Redis에 캐싱합니다.
- 시연 단계에서는 mock STT/Translation을 사용해 API 비용을 줄입니다.
- 대규모 서비스에서는 지역별 CDN과 adaptive bitrate streaming을 사용합니다.
- 오래된 자막 세그먼트는 lifecycle policy로 삭제합니다.
