import React, { useEffect, useMemo, useRef, useState } from 'react';
import Hls from 'hls.js';
import EdgeMap from './EdgeMap';
import {
  LOCATIONS,
  checkAllEdges,
  pickEdge,
  pickSubtitleSource,
  probeSubtitleFetch,
} from '../lib/edgeRouting';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';
const FALLBACK_HLS_URL = import.meta.env.VITE_HLS_URL || 'http://localhost:8080/hls/live/smooth/index.m3u8';
const HLS_PATH = '/hls/live/smooth/index.m3u8';

const LANGUAGES = [
  { code: 'original', label: '한국어', short: 'KO' },
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'ja', label: '日本語', short: 'JA' },
  { code: 'zh', label: '中文', short: 'ZH' },
];

function formatTime(seconds) {
  if (!Number.isFinite(Number(seconds))) return '0:00';
  const value = Number(seconds);
  const mins = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

function seekToLiveEdge(video, offset = 1.5) {
  if (!video?.seekable?.length) return;
  const liveEdge = video.seekable.end(video.seekable.length - 1);
  const target = Math.max(video.seekable.start(video.seekable.length - 1), liveEdge - offset);
  if (Number.isFinite(target) && Math.abs(video.currentTime - target) > 1) {
    video.currentTime = target;
  }
}

function StreamPlayer({ streamId }) {
  const [connected, setConnected] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [language, setLanguage] = useState('original');
  const [stream, setStream] = useState(null);
  const [currentSubtitle, setCurrentSubtitle] = useState(null);
  const [subtitles, setSubtitles] = useState([]);
  const [viewers, setViewers] = useState(0);
  const [health, setHealth] = useState(null);
  const videoRef = useRef(null);
  const historyRef = useRef(null);
  const hlsRef = useRef(null);
  const captionSessionStartedRef = useRef(false);
  const [locationId, setLocationId] = useState('busan');
  const [healthMap, setHealthMap] = useState({ kr: true, jp: true, cn: true, us: true });
  const [busyEdgeIds, setBusyEdgeIds] = useState(new Set());
  const [subtitleProbe, setSubtitleProbe] = useState(null);
  // streamLive: OBS 송출이 들어와 HLS 매니페스트가 정상 파싱되었는지 여부
  // null = 검사 중, true = 방송 들어옴, false = 방송 미수신/끊김
  const [streamLive, setStreamLive] = useState(null);
  const [streamError, setStreamError] = useState(null);

  const currentEdge = useMemo(() => pickEdge(locationId, healthMap), [locationId, healthMap]);
  const subtitleRoute = useMemo(
    () => pickSubtitleSource(language, currentEdge),
    [language, currentEdge],
  );
  const hlsSrc = useMemo(() => {
    if (currentEdge) return `${currentEdge.url}${HLS_PATH}`;
    return FALLBACK_HLS_URL;
  }, [currentEdge]);

  const sttStatus = playing ? 'Live STT running' : 'Live STT ready';

  const selectedText = useMemo(() => {
    if (!currentSubtitle) return '';
    if (language === 'original') return currentSubtitle.original_text;
    return currentSubtitle.translations?.[language] || currentSubtitle.original_text;
  }, [currentSubtitle, language]);

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((response) => response.json())
      .then(setHealth)
      .catch(() => setHealth({ status: 'offline', redis: 'unknown' }));

    fetch(`${API_URL}/streams/${streamId}`)
      .then((response) => response.json())
      .then(setStream)
      .catch(() => setStream(null));

    setSubtitles([]);
    setCurrentSubtitle(null);
  }, [streamId]);

  useEffect(() => {
    const socket = new WebSocket(`${WS_URL}/ws/stream/${streamId}`);

    socket.addEventListener('open', () => setConnected(true));

    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);

      if (message.type === 'subtitle') {
        setCurrentSubtitle(message.data);
        setSubtitles((previous) => {
          const withoutDuplicate = previous.filter((item) => item.id !== message.data.id);
          return [...withoutDuplicate, message.data]
            .sort((a, b) => a.timestamp - b.timestamp)
            .slice(-30);
        });
      }

      if (message.type === 'subtitle_reset') {
        setCurrentSubtitle(null);
        setSubtitles([]);
      }

      if (message.type === 'viewer_update') {
        setViewers(message.data.viewers);
      }

      if (message.type === 'stream_started') {
        setStream(message.data);
      }

      if (message.type === 'stream_stopped') {
        setPlaying(false);
      }
    });

    socket.addEventListener('close', () => {
      setConnected(false);
      setPlaying(false);
    });

    socket.addEventListener('error', () => setConnected(false));

    return () => socket.close();
  }, [streamId]);

  // HLS 바인딩 — hlsSrc가 바뀌면(엣지 페일오버 등) 재바인딩.
  // 매니페스트 파싱 성공/실패로 streamLive 갱신.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return undefined;

    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    setStreamLive(null);
    setStreamError(null);

    if (Hls.isSupported()) {
      const hls = new Hls({
        backBufferLength: 30,
        lowLatencyMode: true,
        liveSyncDurationCount: 1,
        liveMaxLatencyDurationCount: 3,
        maxLiveSyncPlaybackRate: 1.5,
        startPosition: -1,
        manifestLoadingMaxRetry: 1,
        manifestLoadingRetryDelay: 1000,
      });
      hls.loadSource(hlsSrc);
      hls.attachMedia(video);
      hlsRef.current = hls;

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setStreamLive(true);
        setStreamError(null);
        seekToLiveEdge(video);
      });

      hls.on(Hls.Events.LEVEL_LOADED, () => {
        if (!video.paused) seekToLiveEdge(video, 1.5);
      });

      hls.on(Hls.Events.ERROR, (_, data) => {
        // 매니페스트 로드 실패 = OBS 미송출 또는 mediamtx 미연결로 간주
        const isManifestLoadFail =
          data.details === Hls.ErrorDetails.MANIFEST_LOAD_ERROR ||
          data.details === Hls.ErrorDetails.MANIFEST_LOAD_TIMEOUT ||
          data.details === Hls.ErrorDetails.MANIFEST_PARSING_ERROR;
        if (isManifestLoadFail) {
          setStreamLive(false);
          setStreamError(data.details);
          return;
        }
        if (data.fatal) {
          // 미디어 에러는 일단 복구 시도
          try { hls.recoverMediaError(); } catch { setStreamLive(false); }
        }
      });

      return () => {
        hls.destroy();
        hlsRef.current = null;
      };
    }

    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = hlsSrc;
      // Safari 등 네이티브 HLS — 매니페스트 도달 여부를 loadedmetadata로 판정
      const onLoadedMeta = () => setStreamLive(true);
      const onError = () => { setStreamLive(false); setStreamError('native-hls-error'); };
      video.addEventListener('loadedmetadata', onLoadedMeta);
      video.addEventListener('error', onError);
      return () => {
        video.removeEventListener('loadedmetadata', onLoadedMeta);
        video.removeEventListener('error', onError);
      };
    }

    setStreamLive(false);
    setStreamError('hls-not-supported');
    return undefined;
  }, [hlsSrc]);

  // 방송 미수신 상태에서 5초마다 재시도 — OBS가 늦게 켜지는 케이스 대응
  useEffect(() => {
    if (streamLive !== false) return undefined;
    const timer = window.setInterval(() => {
      const hls = hlsRef.current;
      if (hls) {
        try { hls.loadSource(hlsSrc); hls.startLoad(); } catch (e) { /* noop */ }
      } else if (videoRef.current?.canPlayType('application/vnd.apple.mpegurl')) {
        videoRef.current.src = hlsSrc;
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [streamLive, hlsSrc]);

  useEffect(() => {
    const history = historyRef.current;
    if (!history) return;
    history.scrollTop = history.scrollHeight;
  }, [subtitles]);

  useEffect(() => {
    if (!playing) return undefined;
    const timer = window.setInterval(() => {
      seekToLiveEdge(videoRef.current, 1.5);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [playing]);

  // 엣지 헬스체크 polling — 3초마다 4개 엣지에 GET /health
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const status = await checkAllEdges();
      if (!cancelled) setHealthMap(status);
    };
    tick();
    const timer = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // 자막 출처 시연 — 현재 라우팅된 곳에서 vtt playlist를 한 번 fetch해
  // 응답 시간/캐시 상태를 UI에 노출. 화면 자막 표시는 WebSocket으로 그대로 사용.
  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      const result = await probeSubtitleFetch(subtitleRoute.source, language);
      if (!cancelled) setSubtitleProbe({ ...result, viaOrigin: subtitleRoute.viaOrigin });
    };
    probe();
    const timer = window.setInterval(probe, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [language, subtitleRoute.source.url, subtitleRoute.viaOrigin]);

  const handleToggleEdge = async (edgeId, isAlive) => {
    setBusyEdgeIds((prev) => {
      const next = new Set(prev);
      next.add(edgeId);
      return next;
    });
    try {
      const action = isAlive ? 'stop' : 'start';
      await fetch(`${API_URL}/api/edges/${edgeId}/${action}`, { method: 'POST' });
      const status = await checkAllEdges();
      setHealthMap(status);
    } catch (err) {
      console.error('[edge-toggle] failed', err);
    } finally {
      setBusyEdgeIds((prev) => {
        const next = new Set(prev);
        next.delete(edgeId);
        return next;
      });
    }
  };

  const startCaptionSession = () => {
    // 라이브 자막 파이프라인 시작 — 백엔드가 mediamtx 음성을 추출해 STT로 보냄
    const params = new URLSearchParams({
      start_at: '0',
      reset: String(!captionSessionStartedRef.current),
      source: 'live',
    });
    fetch(`${API_URL}/streams/${streamId}/caption-demo/start?${params.toString()}`, { method: 'POST' })
      .then(() => { captionSessionStartedRef.current = true; })
      .catch(() => {});
  };

  const stopCaptionSession = () => {
    fetch(`${API_URL}/streams/${streamId}/caption-demo/stop`, { method: 'POST' }).catch(() => {});
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      if (streamLive !== true) return; // 방송 안 들어왔으면 재생 불가
      startCaptionSession();
      seekToLiveEdge(video);
      video.play().then(() => seekToLiveEdge(video)).catch(() => setPlaying(false));
    } else {
      video.pause();
      stopCaptionSession();
    }
  };

  const openFullscreen = () => {
    videoRef.current?.parentElement?.requestFullscreen?.();
  };

  return (
    <main className="app-shell">
      <section className="top-bar">
        <div>
          <p className="eyebrow">Cloud Computing Term Project</p>
          <h1>{stream?.title || 'Global Live Demo'}</h1>
        </div>
        <div className="status-grid">
          <span className={connected ? 'status good' : 'status bad'}>
            {connected ? 'WebSocket Connected' : 'Disconnected'}
          </span>
          <span className="status">Viewers {viewers}</span>
          <span className="status">Cache {health?.redis || 'checking'}</span>
          <span className={streamLive ? 'status good' : 'status bad'}>
            {streamLive === null ? 'Stream checking...' : streamLive ? 'Stream LIVE' : 'Stream OFFLINE'}
          </span>
          <span className={playing ? 'status good' : 'status'}>{sttStatus}</span>
          <span className="status">Audio 1.5s / Buffer 3s</span>
        </div>
      </section>

      <section className="player-layout">
        <div className="video-panel">
          <div className="video-frame">
            <video
              ref={videoRef}
              className="video"
              preload="metadata"
              playsInline
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => { setPlaying(false); stopCaptionSession(); }}
            />
            <div className="live-indicator">
              <span />
              LIVE
            </div>
            {streamLive !== true && (
              <div className="stream-offline-overlay">
                <strong>📡 방송이 들어오지 않습니다</strong>
                <p>
                  {streamLive === null
                    ? 'HLS 매니페스트 확인 중...'
                    : 'OBS에서 RTMP 송출을 시작해 주세요. 자동으로 5초마다 재시도합니다.'}
                </p>
                {streamError && <small>error: {streamError}</small>}
              </div>
            )}
            <div className="subtitle-layer">
              <p>{selectedText || '실시간 자막을 기다리는 중입니다.'}</p>
              {currentSubtitle && <small>{formatTime(currentSubtitle.timestamp)}</small>}
            </div>
            <div className="controls">
              <button type="button" onClick={togglePlay} disabled={streamLive !== true}>
                {playing ? 'Pause' : 'Play'}
              </button>
              <button type="button" onClick={openFullscreen}>Fullscreen</button>
            </div>
          </div>
        </div>

        <aside className="history">
          <div className="section-title">
            <h2>Live Caption Log</h2>
            <p>{subtitles.length} captions</p>
          </div>
          <div className="history-list" ref={historyRef}>
            {subtitles.length === 0 ? (
              <div className="empty-history">
                <strong>Waiting for speech</strong>
                <p>OBS에서 들어온 음성이 인식되면 이곳에 자막이 시간순으로 쌓입니다.</p>
              </div>
            ) : (
              subtitles.map((subtitle) => (
                <article
                  key={subtitle.id}
                  className={currentSubtitle?.id === subtitle.id ? 'caption-row active' : 'caption-row'}
                >
                  <span>{formatTime(subtitle.timestamp)}</span>
                  <p>
                    {language === 'original'
                      ? subtitle.original_text
                      : subtitle.translations?.[language] || subtitle.original_text}
                  </p>
                </article>
              ))
            )}
          </div>
        </aside>
      </section>

      <section className="edge-panel">
        <div className="edge-header">
          <div>
            <h2>Global Edge Network</h2>
            <p>위치 기반 라우팅 + 페일오버 시연</p>
          </div>
          <label className="location-select">
            <span>Your Location</span>
            <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
              {LOCATIONS.map((loc) => (
                <option key={loc.id} value={loc.id}>{loc.label}</option>
              ))}
            </select>
          </label>
        </div>

        <EdgeMap
          healthMap={healthMap}
          currentEdgeId={currentEdge?.id}
          onToggleEdge={handleToggleEdge}
          busyEdgeIds={busyEdgeIds}
        />

        <div className="routing-info">
          <div className="route-row">
            <span className="route-label">Video (.ts / m3u8)</span>
            <span className="route-value">
              {currentEdge ? `${currentEdge.label} — ${currentEdge.url}` : 'All edges down → Origin fallback'}
            </span>
          </div>
          <div className="route-row">
            <span className="route-label">Subtitle ({language.toUpperCase()})</span>
            <span className="route-value">
              {subtitleRoute.source.label} — {subtitleRoute.source.url}
              {subtitleRoute.viaOrigin && currentEdge && (
                <em> &nbsp; ↳ {currentEdge.label}는 {language.toUpperCase()} 자막 미보유 → 오리진 직접</em>
              )}
            </span>
          </div>
          {subtitleProbe && (
            <div className="route-row probe">
              <span className="route-label">Last vtt probe</span>
              <span className="route-value">
                {subtitleProbe.skipped
                  ? `SKIP  ·  ${subtitleProbe.note}`
                  : subtitleProbe.ok
                  ? `HTTP ${subtitleProbe.status}  ·  ${subtitleProbe.elapsedMs}ms  ·  cache=${subtitleProbe.cacheStatus}  ·  edge-delay=${subtitleProbe.edgeDelayMs}ms`
                  : `FAIL (${subtitleProbe.status || subtitleProbe.error})`}
              </span>
            </div>
          )}
        </div>
      </section>

      <section className="settings-panel">
        <div>
          <h2>Subtitle Language</h2>
          <div className="language-grid">
            {LANGUAGES.map((item) => (
              <button
                type="button"
                key={item.code}
                className={language === item.code ? 'language active' : 'language'}
                onClick={() => setLanguage(item.code)}
              >
                <strong>{item.short}</strong>
                <span>{item.label}</span>
              </button>
            ))}
          </div>
        </div>

      </section>
    </main>
  );
}

export default StreamPlayer;
