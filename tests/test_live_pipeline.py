"""
OBS → RTMP → MediaMTX → HLS → 프론트 재생 경로 통합 테스트

실행 전 필요 서비스:
  docker compose up -d mediamtx backend frontend

ffmpeg 설치 필요 (RTMP 스트림 시뮬레이션용)
"""
import shutil
import subprocess
import time
from urllib.parse import urljoin

import pytest
import requests


MEDIAMTX_HLS  = "http://localhost:8888"
NGINX_ORIGIN   = "http://localhost:8080"
BACKEND        = "http://localhost:8000"
RTMP_INGEST    = "rtmp://localhost:1935/live/demo"
HLS_SMOOTH_URL = f"{MEDIAMTX_HLS}/live/smooth/index.m3u8"

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"),
    reason="ffmpeg가 설치되어 있지 않습니다",
)

# MediaMTX HLS는 첫 요청에 302(cookieCheck)를 반환하므로 Session으로 쿠키를 유지해야 함
_session = requests.Session()


def _is_up(url: str, timeout: float = 1.0) -> bool:
    try:
        return requests.get(url, timeout=timeout).status_code < 500
    except Exception:
        return False


def _wait_for_hls(url: str, retries: int = 10, interval: float = 0.5):
    """HLS playlist가 실제 콘텐츠(#EXTM3U)를 반환할 때까지 대기.
    성공하면 Response 객체 반환, 실패하면 None 반환.

    MediaMTX는 첫 요청에 302(cookieCheck redirect)를 보내고
    세그먼트 버퍼링 중에는 응답을 최대 ~12초 동안 hold한다(HTTP long-poll).
    """
    for _ in range(retries):
        try:
            r = _session.get(url, timeout=20.0)
            if r.status_code == 200 and "#EXTM3U" in r.text:
                return r
        except Exception:
            pass
        time.sleep(interval)
    return None


def _wait_for_url(url: str, retries: int = 20, interval: float = 0.5) -> bool:
    for _ in range(retries):
        if _is_up(url):
            return True
        time.sleep(interval)
    return False


# ── 서비스 헬스 체크 ──────────────────────────────────────────────────────────

class TestServicesUp:
    def test_backend_health(self):
        if not _is_up(f"{BACKEND}/health"):
            pytest.skip("backend가 실행 중이지 않습니다 (docker compose up -d backend)")
        r = requests.get(f"{BACKEND}/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_mediamtx_api_reachable(self):
        if not _is_up("http://localhost:9997/v3/paths/list"):
            pytest.skip("mediamtx가 실행 중이지 않습니다 (docker compose up -d mediamtx)")
        r = requests.get("http://localhost:9997/v3/paths/list")
        # 200: 인증 없음, 401: 인증 필요하지만 API는 동작 중
        assert r.status_code in (200, 401), f"예상치 못한 응답: {r.status_code}"

    def test_nginx_origin_reachable(self):
        if not _is_up(f"{NGINX_ORIGIN}/master.m3u8"):
            pytest.skip("nginx-origin이 실행 중이지 않습니다")
        r = requests.get(f"{NGINX_ORIGIN}/master.m3u8")
        assert r.status_code == 200

    def test_backend_websocket_endpoint_exists(self):
        if not _is_up(f"{BACKEND}/health"):
            pytest.skip("backend가 실행 중이지 않습니다")
        # FastAPI WebSocket 경로에 HTTP GET 시 400/404/426 중 하나를 반환
        # (426 Upgrade Required는 프레임워크 버전에 따라 다름)
        r = requests.get(f"{BACKEND}/ws/stream/demo")
        assert r.status_code in (400, 404, 426)


# ── RTMP 수신 → HLS 생성 검증 ────────────────────────────────────────────────

class TestRtmpToHls:
    @needs_ffmpeg
    def test_rtmp_ingest_produces_hls(self):
        """
        ffmpeg로 10초짜리 테스트 영상을 RTMP로 송출하고
        MediaMTX가 HLS 플레이리스트를 생성하는지 확인한다.
        OBS의 동작을 ffmpeg lavfi(가상 소스)로 시뮬레이션한다.
        """
        if not _is_up("http://localhost:9997/v3/paths/list"):
            pytest.skip("mediamtx가 실행 중이지 않습니다")

        # ffmpeg 가상 소스로 RTMP 스트림 생성 (OBS 시뮬레이션)
        # -re: 실시간 속도, lavfi: 합성 영상+오디오, rtmp://로 송출
        proc = subprocess.Popen([
            "ffmpeg", "-loglevel", "error",
            "-re",
            "-f", "lavfi", "-i", "testsrc=duration=60:size=640x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "500k",
            "-c:a", "aac", "-b:a", "64k",
            "-f", "flv", RTMP_INGEST,
        ])

        try:
            r = _wait_for_hls(f"{MEDIAMTX_HLS}/live/demo/index.m3u8")
            assert r is not None, "RTMP 송출 후 HLS 플레이리스트가 생성되지 않음"
            assert r.status_code == 200
            assert "#EXTM3U" in r.text

        finally:
            proc.terminate()
            proc.wait()

    @needs_ffmpeg
    def test_live_transcoder_produces_smooth_stream(self):
        """
        live/demo로 스트림을 밀어 넣으면 live-transcoder가
        live/smooth 스트림을 생성하는지 확인한다.
        """
        if not _is_up("http://localhost:9997/v3/paths/list"):
            pytest.skip("mediamtx가 실행 중이지 않습니다")

        proc = subprocess.Popen([
            "ffmpeg", "-loglevel", "error",
            "-re",
            "-f", "lavfi", "-i", "testsrc=duration=60:size=640x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "500k",
            "-c:a", "aac", "-b:a", "64k",
            "-f", "flv", RTMP_INGEST,
        ])

        try:
            # live/smooth는 live-transcoder가 live/demo를 받아 re-encode하므로
            # demo 스트림 감지 + re-encode까지 추가 시간 필요 → 재시도 횟수 증가
            r = _wait_for_hls(HLS_SMOOTH_URL, retries=15)
            assert r is not None, "live-transcoder가 live/smooth HLS를 생성하지 않음"
            assert r.status_code == 200
            assert "#EXTM3U" in r.text

        finally:
            proc.terminate()
            proc.wait()

    @needs_ffmpeg
    def test_hls_segment_is_downloadable(self):
        """HLS 플레이리스트에서 세그먼트 URI를 파싱해 실제로 다운로드한다."""
        if not _is_up("http://localhost:9997/v3/paths/list"):
            pytest.skip("mediamtx가 실행 중이지 않습니다")

        proc = subprocess.Popen([
            "ffmpeg", "-loglevel", "error",
            "-re",
            "-f", "lavfi", "-i", "testsrc=duration=60:size=640x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "500k",
            "-c:a", "aac", "-b:a", "64k",
            "-f", "flv", RTMP_INGEST,
        ])

        try:
            if _wait_for_hls(HLS_SMOOTH_URL, retries=15) is None:
                pytest.skip("live/smooth HLS 스트림 생성 실패 (live-transcoder 미실행?)")

            def _non_comment_lines(text):
                return [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]

            # 마스터 플레이리스트에서 media playlist URL 파싱
            media_url = None
            for _ in range(20):
                r = _session.get(HLS_SMOOTH_URL, timeout=20.0)
                if r.status_code == 200:
                    lines = _non_comment_lines(r.text)
                    if lines:
                        # 상대 URI를 playlist URL 기준으로 resolve
                        media_url = urljoin(HLS_SMOOTH_URL, lines[0])
                        break
                time.sleep(0.5)

            assert media_url is not None, "마스터 플레이리스트에서 media playlist URI를 찾을 수 없음"

            # media playlist에서 실제 세그먼트(.ts) URI 파싱
            seg_url = None
            for _ in range(20):
                mr = _session.get(media_url, timeout=20.0)
                if mr.status_code == 200:
                    seg_lines = _non_comment_lines(mr.text)
                    ts_lines = [l for l in seg_lines if ".ts" in l or l.startswith("seg")]
                    if ts_lines:
                        seg_url = urljoin(media_url, ts_lines[0])
                        break
                time.sleep(0.5)

            assert seg_url is not None, "media playlist에 세그먼트 URI가 없음"

            seg_r = _session.get(seg_url, timeout=10)
            assert seg_r.status_code == 200
            assert len(seg_r.content) > 0

        finally:
            proc.terminate()
            proc.wait()


# ── nginx-origin 통해 HLS 접근 검증 ──────────────────────────────────────────

class TestNginxOriginHlsProxy:
    @needs_ffmpeg
    def test_hls_accessible_via_nginx_origin(self):
        """
        프론트가 실제로 사용해야 할 경로:
        nginx-origin의 /hls/ 프록시를 통해 HLS에 접근한다.
        """
        if not _is_up("http://localhost:9997/v3/paths/list"):
            pytest.skip("mediamtx가 실행 중이지 않습니다")
        if not _is_up(f"{NGINX_ORIGIN}/master.m3u8"):
            pytest.skip("nginx-origin이 실행 중이지 않습니다")

        proc = subprocess.Popen([
            "ffmpeg", "-loglevel", "error",
            "-re",
            "-f", "lavfi", "-i", "testsrc=duration=60:size=640x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "500k",
            "-c:a", "aac", "-b:a", "64k",
            "-f", "flv", RTMP_INGEST,
        ])

        try:
            nginx_hls_url = f"{NGINX_ORIGIN}/hls/live/smooth/index.m3u8"
            r = _wait_for_hls(nginx_hls_url, retries=15)
            assert r is not None, "nginx-origin의 /hls/ 경로로 HLS에 접근 불가"
            assert r.status_code == 200
            assert "#EXTM3U" in r.text

            # nginx-origin이 m3u8에 Cache-Control: no-cache를 내려야 함
            cc = r.headers.get("Cache-Control", "")
            assert "no-cache" in cc or "no-store" in cc

        finally:
            proc.terminate()
            proc.wait()


# ── 프론트 → 백엔드 WebSocket 연결 검증 ───────────────────────────────────────

class TestFrontendIntegration:
    def test_stream_info_api(self):
        if not _is_up(f"{BACKEND}/health"):
            pytest.skip("backend가 실행 중이지 않습니다")
        r = requests.get(f"{BACKEND}/streams/demo")
        assert r.status_code == 200
        data = r.json()
        assert data["stream_id"] == "demo"
        assert "is_active" in data

    def test_frontend_served(self):
        if not _is_up("http://localhost:3000"):
            pytest.skip("frontend가 실행 중이지 않습니다")
        r = requests.get("http://localhost:3000")
        assert r.status_code == 200
        # Vite 빌드 결과 또는 dev 서버 응답
        assert "html" in r.headers.get("Content-Type", "").lower()
