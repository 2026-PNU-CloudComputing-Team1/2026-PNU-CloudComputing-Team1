import React, { useEffect, useState, useCallback } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const HLS_URL = import.meta.env.VITE_HLS_URL || 'http://localhost:8080/hls/live/smooth/index.m3u8';

async function fetchHlsLatency() {
  const res = await fetch(HLS_URL, { cache: 'no-store' });
  if (!res.ok) return null;
  const text = await res.text();
  const durations = [...text.matchAll(/#EXTINF:([\d.]+)/g)].map((m) => parseFloat(m[1]));
  if (!durations.length) return null;
  const bufferSec = durations.reduce((a, b) => a + b, 0);
  return { segmentCount: durations.length, bufferSec: Math.round(bufferSec * 10) / 10 };
}

const EDGE_LABELS = { kr: 'Korea (KR)', jp: 'Japan (JP)', cn: 'China (CN)', us: 'USA (US)' };

const EDGE_URLS = {
  kr: import.meta.env.VITE_EDGE_KR_URL || 'http://localhost:8081',
  jp: import.meta.env.VITE_EDGE_JP_URL || 'http://localhost:8082',
  cn: import.meta.env.VITE_EDGE_CN_URL || 'http://localhost:8083',
  us: import.meta.env.VITE_EDGE_US_URL || 'http://localhost:8084',
};

async function measureEdgeRtt(edgeId) {
  const url = `${EDGE_URLS[edgeId]}/health`;
  const t0 = performance.now();
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const res = await fetch(url, { method: 'GET', cache: 'no-store', signal: controller.signal });
    clearTimeout(timer);
    const rtt = Math.round(performance.now() - t0);
    return { ok: res.ok, rttMs: rtt };
  } catch {
    return { ok: false, rttMs: null };
  }
}

async function measureAllEdgeRtts() {
  const entries = await Promise.all(
    Object.keys(EDGE_LABELS).map(async (id) => [id, await measureEdgeRtt(id)])
  );
  return Object.fromEntries(entries);
}

function DelayBar({ value, max, color }) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ flex: 1, background: '#2a2a2a', borderRadius: 4, height: 8, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, background: color, height: '100%', transition: 'width 0.3s' }} />
      </div>
      <span style={{ width: 48, textAlign: 'right', fontSize: 12, color: '#ccc' }}>
        {value.toFixed(1)}s
      </span>
    </div>
  );
}

function MetricRow({ m }) {
  const maxDelay = Math.max(m.e2e_delay, 15);
  return (
    <div style={{
      background: '#1e1e1e',
      border: '1px solid #333',
      borderRadius: 8,
      padding: '12px 16px',
      marginBottom: 10,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ color: '#888', fontSize: 12 }}>seg {String(m.segment_num).padStart(4, '0')}</span>
        <span style={{ color: '#555', fontSize: 11 }}>{m.recorded_at?.slice(11, 19) ?? ''}</span>
      </div>

      {/* 지연 시간 바 */}
      <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: '4px 8px', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ color: '#aaa', fontSize: 12 }}>버퍼 누적</span>
        <DelayBar value={m.buffer_wait} max={maxDelay} color="#6c8ebf" />
        <span style={{ color: '#aaa', fontSize: 12 }}>STT (Whisper)</span>
        <DelayBar value={m.stt_delay} max={maxDelay} color="#82b366" />
        <span style={{ color: '#aaa', fontSize: 12 }}>번역 (Google)</span>
        <DelayBar value={m.translation_delay} max={maxDelay} color="#d6b656" />
        <span style={{ color: '#fff', fontSize: 12, fontWeight: 600 }}>E2E 지연</span>
        <DelayBar value={m.e2e_delay} max={maxDelay} color="#ae4132" />
      </div>

      {/* 텍스트 */}
      <div style={{ borderTop: '1px solid #2a2a2a', paddingTop: 8 }}>
        <div style={{ marginBottom: 4 }}>
          <span style={{ color: '#888', fontSize: 11 }}>원문 </span>
          <span style={{ color: '#e8e8e8', fontSize: 13 }}>{m.original_text}</span>
        </div>
        {m.translations && Object.entries(m.translations).map(([lang, txt]) => (
          <div key={lang} style={{ display: 'flex', gap: 6 }}>
            <span style={{ color: '#555', fontSize: 11, width: 20, textAlign: 'right', flexShrink: 0 }}>
              {lang.toUpperCase()}
            </span>
            <span style={{ color: '#b0c4de', fontSize: 12 }}>{txt}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function rttColor(rttMs) {
  if (rttMs === null) return '#bf6d6d';
  if (rttMs < 30) return '#6dbf6d';
  if (rttMs < 100) return '#d6b656';
  return '#bf6d6d';
}

function EdgePanel({ edges, rtts }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 24 }}>
      {Object.entries(EDGE_LABELS).map(([id, label]) => {
        const info = edges?.[id];
        const running = info?.running ?? false;
        const rtt = rtts?.[id];
        const rttMs = rtt?.rttMs ?? null;
        return (
          <div key={id} style={{
            background: '#1e1e1e',
            border: `1px solid ${running ? '#3a5a3a' : '#5a3a3a'}`,
            borderRadius: 8,
            padding: '10px 14px',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ color: '#ccc', fontSize: 13 }}>{label}</span>
              <span style={{
                fontSize: 11,
                fontWeight: 600,
                color: running ? '#6dbf6d' : '#bf6d6d',
                background: running ? '#1a3a1a' : '#3a1a1a',
                padding: '2px 8px',
                borderRadius: 4,
              }}>
                {running ? 'RUNNING' : 'STOPPED'}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: '#666', fontSize: 11 }}>RTT</span>
              <div style={{ flex: 1, background: '#2a2a2a', borderRadius: 4, height: 6, overflow: 'hidden' }}>
                <div style={{
                  width: rttMs !== null ? `${Math.min((rttMs / 300) * 100, 100)}%` : '100%',
                  background: rttColor(rttMs),
                  height: '100%',
                  transition: 'width 0.5s',
                }} />
              </div>
              <span style={{ fontSize: 12, color: rttColor(rttMs), width: 52, textAlign: 'right' }}>
                {rttMs !== null ? `${rttMs}ms` : 'N/A'}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function StreamLatencyPanel({ hlsLatency }) {
  if (!hlsLatency) {
    return (
      <div style={{
        background: '#1e1e1e', border: '1px solid #333', borderRadius: 8,
        padding: '12px 16px', marginBottom: 24, color: '#555', fontSize: 13,
      }}>
        HLS 스트림에 연결할 수 없습니다 (스트림 미시작 또는 CORS 미설정)
      </div>
    );
  }
  const { segmentCount, bufferSec } = hlsLatency;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 24,
    }}>
      <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: 8, padding: '10px 14px', textAlign: 'center' }}>
        <div style={{ color: '#6c8ebf', fontSize: 22, fontWeight: 700 }}>{bufferSec}s</div>
        <div style={{ color: '#888', fontSize: 11, marginTop: 2 }}>HLS 버퍼 깊이 (영상+음성 지연)</div>
      </div>
      <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: 8, padding: '10px 14px', textAlign: 'center' }}>
        <div style={{ color: '#aaa', fontSize: 22, fontWeight: 700 }}>{segmentCount}</div>
        <div style={{ color: '#888', fontSize: 11, marginTop: 2 }}>플레이리스트 세그먼트 수</div>
      </div>
    </div>
  );
}

function SummaryBar({ metrics }) {
  if (!metrics.length) return null;
  const avg = (key) => (metrics.reduce((s, m) => s + (m[key] ?? 0), 0) / metrics.length).toFixed(1);
  const items = [
    { label: '버퍼 누적 평균', value: avg('buffer_wait'), color: '#6c8ebf' },
    { label: 'STT 평균', value: avg('stt_delay'), color: '#82b366' },
    { label: '번역 평균', value: avg('translation_delay'), color: '#d6b656' },
    { label: 'E2E 평균', value: avg('e2e_delay'), color: '#ae4132' },
  ];
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(4, 1fr)',
      gap: 10,
      marginBottom: 24,
    }}>
      {items.map(({ label, value, color }) => (
        <div key={label} style={{
          background: '#1e1e1e',
          border: `1px solid #333`,
          borderRadius: 8,
          padding: '10px 14px',
          textAlign: 'center',
        }}>
          <div style={{ color, fontSize: 22, fontWeight: 700 }}>{value}s</div>
          <div style={{ color: '#888', fontSize: 11, marginTop: 2 }}>{label}</div>
        </div>
      ))}
    </div>
  );
}

export default function AdminPage() {
  const [metrics, setMetrics] = useState([]);
  const [edges, setEdges] = useState({});
  const [edgeRtts, setEdgeRtts] = useState({});
  const [hlsLatency, setHlsLatency] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      const [mRes, eRes, hls, rtts] = await Promise.all([
        fetch(`${API_URL}/api/admin/metrics?limit=20`),
        fetch(`${API_URL}/api/edges/status`),
        fetchHlsLatency(),
        measureAllEdgeRtts(),
      ]);
      if (mRes.ok) {
        const data = await mRes.json();
        setMetrics(data.metrics ?? []);
      }
      if (eRes.ok) {
        const data = await eRes.json();
        setEdges(data.edges ?? {});
      }
      setHlsLatency(hls);
      setEdgeRtts(rtts);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch {
      // network error — keep stale data
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 3000);
    return () => clearInterval(id);
  }, [fetchAll]);

  return (
    <div style={{
      minHeight: '100vh',
      background: '#141414',
      color: '#e8e8e8',
      fontFamily: 'monospace',
      padding: '24px 32px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 20, color: '#fff' }}>관리자 대시보드</h1>
        <span style={{ color: '#555', fontSize: 12 }}>
          {lastUpdated ? `마지막 갱신: ${lastUpdated}` : '로딩 중...'}
        </span>
      </div>

      <h2 style={{ fontSize: 13, color: '#888', margin: '0 0 12px', textTransform: 'uppercase', letterSpacing: 1 }}>
        영상 / 음성 스트림 지연
      </h2>
      <StreamLatencyPanel hlsLatency={hlsLatency} />

      <h2 style={{ fontSize: 13, color: '#888', margin: '0 0 12px', textTransform: 'uppercase', letterSpacing: 1 }}>
        엣지 상태 / RTT
      </h2>
      <EdgePanel edges={edges} rtts={edgeRtts} />

      <h2 style={{ fontSize: 13, color: '#888', margin: '0 0 12px', textTransform: 'uppercase', letterSpacing: 1 }}>
        지연 시간 요약 (최근 {metrics.length}개 세그먼트)
      </h2>
      <SummaryBar metrics={metrics} />

      <h2 style={{ fontSize: 13, color: '#888', margin: '0 0 12px', textTransform: 'uppercase', letterSpacing: 1 }}>
        세그먼트별 지연 시간 / 텍스트
      </h2>
      {metrics.length === 0 ? (
        <div style={{ color: '#555', fontSize: 14, padding: '20px 0' }}>
          아직 수신된 세그먼트가 없습니다. 스트림을 시작하면 데이터가 표시됩니다.
        </div>
      ) : (
        metrics.map((m) => <MetricRow key={`${m.segment_num}-${m.recorded_at}`} m={m} />)
      )}
    </div>
  );
}
