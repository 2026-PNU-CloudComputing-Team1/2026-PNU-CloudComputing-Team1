import React, { useEffect, useMemo, useRef, useState } from 'react';
import Hls from 'hls.js';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';
const HLS_URL = import.meta.env.VITE_HLS_URL || 'http://localhost:8888/live/smooth/index.m3u8';
const MP4_URL = `${API_URL}/media/AWS.mp4`;

const LANGUAGES = [
  { code: 'original', label: '한국어', short: 'KO' },
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'ja', label: '日本語', short: 'JA' },
  { code: 'zh', label: '中文', short: 'ZH' },
];

const PIPELINE_STEPS = [
  'OBS RTMP ingest',
  'MediaMTX RTMP routing',
  'FFmpeg live-transcoder',
  'HLS playback with hls.js',
  'FFmpeg audio segments',
  'Whisper STT via Redis queue',
  'WebSocket caption delivery',
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
  const [pendingSubtitles, setPendingSubtitles] = useState([]);
  const [viewers, setViewers] = useState(0);
  const [health, setHealth] = useState(null);
  const videoRef = useRef(null);
  const historyRef = useRef(null);
  const hlsRef = useRef(null);
  const demoStartedRef = useRef(false);
  const captionSessionStartedRef = useRef(false);
  const acceptCaptionsRef = useRef(false);
  const [mode, setMode] = useState('demo');
  const [videoSource, setVideoSource] = useState('mp4');

  const sttStatus = useMemo(() => {
    if (mode === 'demo') return playing ? 'Demo STT running' : 'Demo STT ready';
    return playing ? 'Live STT running' : 'Live STT ready';
  }, [mode, playing]);

  const selectedText = useMemo(() => {
    if (!currentSubtitle) return '';
    if (language === 'original') return currentSubtitle.original_text;
    return currentSubtitle.translations?.[language] || currentSubtitle.original_text;
  }, [currentSubtitle, language]);

  useEffect(() => {
    fetch(`${API_URL}/streams/${streamId}/caption-demo/stop`, { method: 'POST' }).catch(() => {});

    fetch(`${API_URL}/health`)
      .then((response) => response.json())
      .then(setHealth)
      .catch(() => setHealth({ status: 'offline', redis: 'unknown' }));

    fetch(`${API_URL}/streams/${streamId}`)
      .then((response) => response.json())
      .then(setStream)
      .catch(() => setStream(null));

    setSubtitles([]);
    setPendingSubtitles([]);
    setCurrentSubtitle(null);
    acceptCaptionsRef.current = false;
  }, [streamId]);

  useEffect(() => {
    const socket = new WebSocket(`${WS_URL}/ws/stream/${streamId}`);

    socket.addEventListener('open', () => {
      setConnected(true);
    });

    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);

      if (message.type === 'subtitle') {
        if (!acceptCaptionsRef.current && mode !== 'live') return;

        if (mode === 'demo') {
          const video = videoRef.current;
          if (video && message.data.timestamp > video.currentTime + 0.3) {
            setPendingSubtitles((previous) => {
              const withoutDuplicate = previous.filter((item) => item.id !== message.data.id);
              return [...withoutDuplicate, message.data].sort((a, b) => a.timestamp - b.timestamp);
            });
            return;
          }
        }

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
        setPendingSubtitles([]);
        acceptCaptionsRef.current = true;
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

    socket.addEventListener('error', () => {
      setConnected(false);
    });

    return () => socket.close();
  }, [mode, streamId]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.currentTime = 0;
    setPlaying(false);
    demoStartedRef.current = false;
    captionSessionStartedRef.current = false;
    acceptCaptionsRef.current = false;
  }, [streamId]);

  useEffect(() => {
    if (!playing || mode !== 'demo' || pendingSubtitles.length === 0) return undefined;

    const timer = window.setInterval(() => {
      const video = videoRef.current;
      if (!video) return;

      setPendingSubtitles((pending) => {
        const visibleUntil = video.currentTime + 0.15;
        const due = pending.filter((item) => item.timestamp <= visibleUntil);
        const waiting = pending.filter((item) => item.timestamp > visibleUntil);

        if (due.length > 0) {
          setCurrentSubtitle(due[due.length - 1]);
          setSubtitles((previous) => {
            const merged = [...previous];
            due.forEach((item) => {
              if (!merged.some((existing) => existing.id === item.id)) {
                merged.push(item);
              }
            });
            return merged.sort((a, b) => a.timestamp - b.timestamp).slice(-30);
          });
        }

        return waiting;
      });
    }, 100);

    return () => window.clearInterval(timer);
  }, [mode, pendingSubtitles.length, playing]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return undefined;

    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    video.pause();
    setPlaying(false);
    acceptCaptionsRef.current = false;
    demoStartedRef.current = false;
    captionSessionStartedRef.current = false;
    setCurrentSubtitle(null);
    setSubtitles([]);
    setPendingSubtitles([]);
    stopCaptionDemo();

    if (mode === 'demo') {
      video.src = MP4_URL;
      video.currentTime = 0;
      setVideoSource('mp4');
      return undefined;
    }

    if (Hls.isSupported()) {
      const hls = new Hls({
        backBufferLength: 30,
        lowLatencyMode: true,
        liveSyncDurationCount: 1,
        liveMaxLatencyDurationCount: 3,
        maxLiveSyncPlaybackRate: 1.5,
        startPosition: -1,
      });
      hls.loadSource(HLS_URL);
      hls.attachMedia(video);
      hlsRef.current = hls;
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setVideoSource('hls');
        seekToLiveEdge(video);
      });
      hls.on(Hls.Events.LEVEL_LOADED, () => {
        if (!video.paused) {
          seekToLiveEdge(video, 1.5);
        }
      });
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) {
          hls.recoverMediaError();
        }
      });
      return () => {
        hls.destroy();
        hlsRef.current = null;
      };
    }

    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = HLS_URL;
      setVideoSource('hls');
      return undefined;
    }

    video.src = MP4_URL;
    setVideoSource('mp4');
    return undefined;
  }, [mode]);

  useEffect(() => {
    const history = historyRef.current;
    if (!history) return;
    history.scrollTop = history.scrollHeight;
  }, [subtitles]);

  useEffect(() => {
    if (!playing || mode !== 'live') return undefined;

    const timer = window.setInterval(() => {
      seekToLiveEdge(videoRef.current, 1.5);
    }, 2000);

    return () => window.clearInterval(timer);
  }, [mode, playing]);

  const stopCaptionDemo = () => {
    fetch(`${API_URL}/streams/${streamId}/caption-demo/stop`, { method: 'POST' }).catch(() => {});
  };

  const switchMode = (nextMode) => {
    if (nextMode === mode) return;
    setMode(nextMode);
  };

  const togglePlay = () => {
    if (!videoRef.current) return;
    const video = videoRef.current;
    if (videoRef.current.paused) {
      if (!demoStartedRef.current) {
        demoStartedRef.current = true;
        acceptCaptionsRef.current = true;
        const useLiveCaptions = mode === 'live';
        const startAt = useLiveCaptions ? 0 : video.currentTime || 0;
        const reset = useLiveCaptions
          ? !captionSessionStartedRef.current
          : startAt < 0.5 || video.ended;

        if (reset) {
          setCurrentSubtitle(null);
          setSubtitles([]);
          setPendingSubtitles([]);
          if (!useLiveCaptions) {
            video.currentTime = 0;
          }
        }

        const params = new URLSearchParams({
          start_at: String(reset ? 0 : startAt),
          reset: String(reset),
          source: useLiveCaptions ? 'live' : 'file',
        });
        fetch(`${API_URL}/streams/${streamId}/caption-demo/start?${params.toString()}`, { method: 'POST' })
          .then(() => {
            captionSessionStartedRef.current = true;
          })
          .catch(() => {
            demoStartedRef.current = false;
          });
      }
      if (mode === 'live') {
        seekToLiveEdge(video);
      }
      videoRef.current.play().then(() => {
        if (mode === 'live') {
          seekToLiveEdge(video);
        }
      }).catch(() => setPlaying(false));
    } else {
      acceptCaptionsRef.current = false;
      videoRef.current.pause();
      stopCaptionDemo();
      demoStartedRef.current = false;
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
          <span className="status">Mode {mode === 'live' ? 'LIVE' : 'DEMO'}</span>
          <span className="status">Video {videoSource.toUpperCase()}</span>
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
              onPause={() => {
                setPlaying(false);
                acceptCaptionsRef.current = false;
              }}
              onEnded={() => {
                setPlaying(false);
                acceptCaptionsRef.current = false;
                stopCaptionDemo();
                demoStartedRef.current = false;
              }}
            />
            <div className="live-indicator">
              <span />
              {mode === 'live' ? 'LIVE' : 'DEMO'}
            </div>
            <div className="subtitle-layer">
              <p>{selectedText || '실시간 자막을 기다리는 중입니다.'}</p>
              {currentSubtitle && <small>{formatTime(currentSubtitle.timestamp)}</small>}
            </div>
            <div className="controls">
              <button type="button" onClick={togglePlay}>{playing ? 'Pause' : 'Play'}</button>
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
                <p>
                  {mode === 'live'
                    ? 'OBS에서 들어온 음성이 인식되면 이곳에 자막이 시간순으로 쌓입니다.'
                    : 'Play를 누르면 데모 음성 인식 결과가 이곳에 쌓입니다.'}
                </p>
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

      <section className="settings-panel">
        <div>
          <h2>Playback Mode</h2>
          <div className="mode-grid">
            <button
              type="button"
              className={mode === 'demo' ? 'mode-button active' : 'mode-button'}
              onClick={() => switchMode('demo')}
            >
              <strong>Demo Video</strong>
              <span>Sample MP4 + file STT test</span>
            </button>
            <button
              type="button"
              className={mode === 'live' ? 'mode-button active' : 'mode-button'}
              onClick={() => switchMode('live')}
            >
              <strong>Live Stream</strong>
              <span>OBS RTMP + HLS + live STT</span>
            </button>
          </div>
        </div>

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

        <div>
          <h2>Implemented Pipeline</h2>
          <ol className="pipeline">
            {PIPELINE_STEPS.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      </section>
    </main>
  );
}

export default StreamPlayer;
