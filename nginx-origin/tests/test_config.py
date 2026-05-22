"""
nginx-origin 단위 테스트 — nginx.conf 문법 검증
nginx -t 로 설정 파일 파싱 오류를 확인
nginx가 설치되어 있지 않으면 skip
"""
import os
import shutil
import subprocess

import pytest

CONF_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "nginx.conf")
)

needs_nginx = pytest.mark.skipif(
    not shutil.which("nginx"),
    reason="nginx가 설치되어 있지 않습니다",
)


# --- 파일 내용 검사 (nginx 설치 불필요) ---

def test_nginx_conf_exists():
    assert os.path.exists(CONF_PATH), f"nginx.conf 파일 없음: {CONF_PATH}"


def test_nginx_conf_contains_required_locations():
    content = open(CONF_PATH).read()
    assert "location = /master.m3u8" in content
    assert "location /subtitles/" in content
    assert "location /hls/" in content


def test_nginx_conf_proxy_buffering_off():
    # 라이브 스트리밍에서 proxy_buffering on이면 지연이 누적됨
    content = open(CONF_PATH).read()
    assert "proxy_buffering    off" in content


def test_nginx_conf_m3u8_no_cache():
    content = open(CONF_PATH).read()
    assert '"no-cache, no-store, must-revalidate"' in content


def test_nginx_conf_vtt_cache():
    content = open(CONF_PATH).read()
    assert '"public, max-age=3600"' in content


# --- 문법 검사 (nginx 설치 필요) ---

def _nginx_prefix() -> str:
    """로컬 nginx의 --prefix 경로를 동적으로 감지."""
    r = subprocess.run(["nginx", "-V"], capture_output=True, text=True)
    for token in r.stderr.split():
        if token.startswith("--prefix="):
            return token.split("=", 1)[1]
    return "/etc/nginx"


@needs_nginx
def test_nginx_conf_syntax_valid(tmp_path):
    # nginx.conf의 include /etc/nginx/mime.types는 Docker 절대경로
    # 로컬 실행 시 mime.types 위치가 다르므로 실제 경로로 교체한 임시 config로 검증
    candidates = [
        "/etc/nginx/mime.types",
        "/opt/homebrew/etc/nginx/mime.types",
        f"{_nginx_prefix()}/conf/mime.types",
    ]
    mime_path = next((p for p in candidates if os.path.exists(p)), None)
    if mime_path is None:
        pytest.skip("mime.types를 찾을 수 없습니다")

    content = (
        open(CONF_PATH).read()
        .replace("include      /etc/nginx/mime.types;", f"include      {mime_path};")
        # mediamtx는 Docker 내부 호스트명 — nginx -t가 DNS 조회를 시도하므로 IP로 교체
        .replace("http://mediamtx:8888/", "http://127.0.0.1:8888/")
    )
    patched = tmp_path / "nginx-patched.conf"
    patched.write_text(content)

    result = subprocess.run(
        ["nginx", "-t", "-c", str(patched)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"nginx -t 실패:\n{result.stderr}"
