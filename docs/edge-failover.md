# Edge Failover 시나리오

위치 기반 엣지 라우팅과 페일오버 동작을 발표 중에 시연하기 위한 구성과 시나리오를 정리한다.

## 1. 구성 요약

- **엣지 4개** (docker-compose 서비스: `edge-korea`, `edge-japan`, `edge-china`, `edge-us`)
  - 각 엣지는 동일한 nginx 이미지지만 `tc netem`으로 인공 지연을 다르게 적용 (KR 10ms / JP 50ms / CN 80ms / US 180ms)
  - 호스트 포트: KR 8081, JP 8082, CN 8083, US 8084
- **오리진**: `nginx-origin:8080` — 모든 콘텐츠의 원본
- **프론트엔드 라우팅**: 사용자가 선택한 위치에 따라 우선순위 배열로 엣지 결정
- **자막 분배 정책**: 엣지마다 한 가지 언어만 보유한다고 가정
  - KR → 한국어(원문), JP → 일본어, CN → 중국어, US → 영어
  - 선택 언어가 현재 엣지에 없으면 오리진에서 직접 자막 fetch

## 2. 도시 ↔ 엣지 우선순위 표

| 도시 | 1순위 | 2순위 | 3순위 | 4순위 |
|------|-------|-------|-------|-------|
| Busan | KR | JP | CN | US |
| Tokyo | JP | KR | CN | US |
| Beijing | CN | KR | JP | US |
| New York | US | JP | KR | CN |
| London | US | KR | JP | CN |
| Sydney | JP | US | CN | KR |

엣지가 없는 나라(런던/시드니)는 지리적으로 가장 가까운 엣지를 1순위로 매핑한다.

## 3. 페일오버 동작 흐름

```text
[프론트엔드]
   │
   ├── 3초마다 4개 엣지에 GET /health
   │     └── 응답 OK/실패 → healthMap[edgeId] 갱신
   │
   ├── pickEdge(locationId, healthMap)
   │     └── 우선순위 배열에서 살아있는 첫 엣지 반환
   │
   ├── currentEdge 변경 감지
   │     ├── 영상: hls.loadSource(`${edge.url}/hls/live/smooth/index.m3u8`)
   │     └── 자막: pickSubtitleSource(language, currentEdge)로 엣지/오리진 결정
   │
   └── "Kill Edge" 버튼
         └── POST /api/edges/{id}/stop → 백엔드가 docker stop → 다음 헬스체크에서 DOWN 감지
```

### 헬스체크 주기와 페일오버 지연

- **감지 시간**: 최대 3초 (헬스체크 polling 간격) + 2초 (HTTP timeout) = **최악 5초**
- **재바인딩**: hls.js `loadSource()` 호출 즉시 → 새 엣지에서 m3u8/ts 받기 시작
- 화면 끊김 시간은 hls.js의 백버퍼(`backBufferLength: 30`)와 라이브 엣지 동기화 정책에 따라 결정됨

## 4. 시연 시나리오

### 시나리오 A — 정상 라우팅 (지리 기반 선택)

1. 페이지 진입 (기본 위치: Busan)
2. **Edge Map**에서 `Korea (Seoul)` 카드에 `USING` 배지가 떠 있는지 확인
3. **Routing Info** 패널 확인:
   - Video → `http://localhost:8081 (Korea ...)`
   - Subtitle (KO) → `http://localhost:8081 (Korea ...)` (엣지 내 보유)
4. 위치를 **Tokyo**로 변경
5. Edge Map에서 USING 배지가 `Japan (Tokyo)`로 이동, Video URL이 `:8082`로 변경되는지 확인
6. Routing Info의 Last vtt probe 줄에서 응답 시간이 변하는 것 확인 (`X-Edge-Delay-Ms: 50` 등)

### 시나리오 B — 엣지 페일오버 (1차 → 2차)

1. 위치를 **Busan**으로 설정 (1순위 KR)
2. Edge Map에서 **Korea (Seoul)**의 `Kill Edge` 클릭
3. 약 1~3초 뒤 Edge Map 상태:
   - KR 카드가 회색(`dead`) + `Status: DOWN`
   - USING 배지가 자동으로 **JP** 카드로 이동
4. Routing Info의 Video URL이 `:8082`로 전환됨
5. hls.js가 새 m3u8을 받아 재생 재개 (화면 일시 멈춤 ≤ 5초)
6. `Revive Edge`로 KR을 다시 살리면 USING 배지가 KR로 복귀

### 시나리오 C — 자막 출처 분기

1. 위치 = Busan (KR 엣지 사용 중), 자막 언어 = **English**
2. Routing Info:
   - Video → `http://localhost:8081 (Korea ...)`
   - Subtitle (EN) → `http://localhost:8080 (Origin Server)`
     - `↳ Korea (Seoul)는 EN 자막 미보유 → 오리진 직접` 라벨 표시
3. 자막 언어를 **Japanese**로 변경 → 여전히 오리진 (KR엔 JA도 없음)
4. 자막을 한국어(KO)로 되돌리면 → Subtitle URL이 KR 엣지로 회귀

### 시나리오 D — 모든 엣지 다운 (오리진 폴백)

1. Edge Map에서 4개 엣지를 모두 `Kill Edge`
2. Routing Info의 Video 줄:
   - `All edges down → Origin fallback`
3. hls.js가 FALLBACK_HLS_URL(`http://localhost:8080/...`)로 바인딩되어 오리진 직접 재생
4. `Revive Edge`로 하나라도 살리면 즉시 그 엣지로 복귀

## 5. 발표 중 자주 받는 질문

**Q. 실제 CDN도 이렇게 자막을 언어별로 분리하나요?**
> 일반적인 글로벌 CDN(Cloudflare/Akamai 등)은 콘텐츠 차별 없이 모든 자산을 어디서든 캐시한다. 본 데모는 "엣지별 콘텐츠 차이"를 시각적으로 보여주기 위한 가공된 시나리오다. 실제로는 오리진에 모든 언어가 있고, 엣지는 처음 요청된 자산을 캐시(pull-through)하는 방식이 더 흔하다.

**Q. 헬스체크 5초가 너무 길지 않나요?**
> 시연용 값이다. 프로덕션 CDN은 BGP anycast나 DNS GSLB로 페일오버를 처리해 클라이언트 측 polling이 필요 없다. 이 프로젝트는 클라이언트가 직접 라우팅을 시뮬레이션하므로 polling 주기가 곧 감지 시간이 된다.

**Q. 자막 텍스트가 vtt 파일에서 오는 게 맞나요?**
> 화면 표시는 WebSocket으로 받은 번역 결과를 사용한다. UI의 `Last vtt probe`는 "엣지/오리진 중 어디서 받는지" 보여주기 위한 시연용 fetch로, 실제 자막 렌더링과는 분리되어 있다.

