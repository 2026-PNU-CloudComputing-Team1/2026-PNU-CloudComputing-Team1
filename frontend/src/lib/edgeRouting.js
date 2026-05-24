// 위치 기반 엣지 라우팅 + 페일오버 + 자막 출처 분기.
// 백엔드/엣지 nginx 변경 없이 프론트만으로 시연 가능하도록 모든 결정 로직을 여기 둠.

const ORIGIN_URL = import.meta.env.VITE_ORIGIN_URL || 'http://localhost:8080';

export const EDGES = {
  kr: {
    id: 'kr',
    label: 'Korea (Seoul)',
    url: import.meta.env.VITE_EDGE_KR_URL || 'http://localhost:8081',
    delayMs: 10,
    subtitleLang: 'original', // 한국어 (원문)
  },
  jp: {
    id: 'jp',
    label: 'Japan (Tokyo)',
    url: import.meta.env.VITE_EDGE_JP_URL || 'http://localhost:8082',
    delayMs: 50,
    subtitleLang: 'ja',
  },
  cn: {
    id: 'cn',
    label: 'China (Beijing)',
    url: import.meta.env.VITE_EDGE_CN_URL || 'http://localhost:8083',
    delayMs: 80,
    subtitleLang: 'zh',
  },
  us: {
    id: 'us',
    label: 'USA (Virginia)',
    url: import.meta.env.VITE_EDGE_US_URL || 'http://localhost:8084',
    delayMs: 180,
    subtitleLang: 'en',
  },
};

export const ORIGIN = { id: 'origin', label: 'Origin Server', url: ORIGIN_URL };

// 사용자가 선택할 수 있는 도시 — 엣지가 없는 곳도 포함해 "가장 가까운" 매핑 시연
// 우선순위 배열: 첫 번째가 가장 가깝고, 뒤로 갈수록 멀어짐. 페일오버 시 이 순서대로 시도.
export const LOCATIONS = [
  { id: 'busan',   label: 'Busan (부산)',      country: 'KR', priority: ['kr', 'jp', 'cn', 'us'] },
  { id: 'tokyo',   label: 'Tokyo (東京)',      country: 'JP', priority: ['jp', 'kr', 'cn', 'us'] },
  { id: 'beijing', label: 'Beijing (北京)',    country: 'CN', priority: ['cn', 'kr', 'jp', 'us'] },
  { id: 'newyork', label: 'New York',          country: 'US', priority: ['us', 'jp', 'kr', 'cn'] },
  { id: 'london',  label: 'London',            country: 'UK', priority: ['us', 'kr', 'jp', 'cn'] },
  { id: 'sydney',  label: 'Sydney',            country: 'AU', priority: ['jp', 'us', 'cn', 'kr'] },
];

// 살아있는 엣지 중 우선순위 1순위 반환. 모두 다운이면 null.
export function pickEdge(locationId, healthMap) {
  const loc = LOCATIONS.find((l) => l.id === locationId);
  if (!loc) return null;
  for (const edgeId of loc.priority) {
    if (healthMap?.[edgeId]) return EDGES[edgeId];
  }
  return null;
}

// 자막 출처 결정: 현재 엣지가 그 언어를 보유 중이면 엣지, 아니면 오리진.
// language 코드는 StreamPlayer의 LANGUAGES와 동일 ('original' | 'en' | 'ja' | 'zh').
export function pickSubtitleSource(language, currentEdge) {
  if (currentEdge && currentEdge.subtitleLang === language) {
    return { source: currentEdge, viaOrigin: false };
  }
  return { source: ORIGIN, viaOrigin: true };
}

// 엣지의 /health에 HEAD 요청 — timeout 2초.
export async function checkEdgeHealth(edge) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    const res = await fetch(`${edge.url}/health`, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
    });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

export async function checkAllEdges() {
  const entries = await Promise.all(
    Object.values(EDGES).map(async (edge) => [edge.id, await checkEdgeHealth(edge)]),
  );
  return Object.fromEntries(entries);
}

// 시연용 vtt 샘플 fetch — 화면 표시는 WebSocket으로 받지만,
// "어디서 받는지" 보여주려고 별도로 한 번 더 가져옴.
// 응답 헤더 X-Cache-Status, X-Edge-Delay-Ms 까지 노출.
export async function probeSubtitleFetch(subtitleSource, language) {
  // 한국어(원문)는 subtitle-pub이 vtt를 생성하지 않음 — probe skip
  if (language === 'original') {
    return {
      ok: true,
      skipped: true,
      sourceLabel: subtitleSource.label,
      note: '원문은 vtt 파일 없이 WebSocket으로 직접 전달',
    };
  }
  const langDir = language;
  const url = `${subtitleSource.url}/subtitles/${langDir}/playlist.m3u8`;
  const start = performance.now();
  try {
    const res = await fetch(url, { cache: 'no-store' });
    const elapsed = Math.round(performance.now() - start);
    return {
      ok: res.ok,
      status: res.status,
      url,
      elapsedMs: elapsed,
      cacheStatus: res.headers.get('x-cache-status') || '-',
      edgeDelayMs: res.headers.get('x-edge-delay-ms') || '-',
      sourceLabel: subtitleSource.label,
    };
  } catch (err) {
    return { ok: false, status: 0, url, error: String(err), sourceLabel: subtitleSource.label };
  }
}
