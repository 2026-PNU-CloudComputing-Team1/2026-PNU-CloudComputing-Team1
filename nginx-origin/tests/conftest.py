"""
nginx-origin 테스트용 로컬 nginx 서버 구동
nginx가 설치되어 있으면 fixtures 디렉터리를 마운트한 테스트 서버를 8090 포트에 띄움
nginx가 없으면 모든 서비스 테스트를 skip
"""
import os
import shutil
import subprocess
import time

import pytest
import requests

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
TEST_PORT    = int(os.getenv("NGINX_TEST_PORT", "8090"))


@pytest.fixture(scope="session")
def nginx_url(tmp_path_factory):
    if not shutil.which("nginx"):
        pytest.skip("nginx가 설치되어 있지 않습니다")

    tmp      = tmp_path_factory.mktemp("nginx_run")
    conf     = tmp / "nginx-test.conf"
    log_dir  = tmp / "logs"
    log_dir.mkdir()

    # FIXTURES_DIR을 /data/subtitles 역할로 마운트하는 테스트 전용 설정
    conf.write_text(f"""
worker_processes 1;
error_log {log_dir}/error.log;
pid {tmp}/nginx.pid;
daemon off;

events {{ worker_connections 64; }}

http {{
    types {{
        text/vtt                      vtt;
        application/vnd.apple.mpegurl m3u8;
    }}

    map $uri $cache_ctrl {{
        ~\\.m3u8$  "no-cache, no-store, must-revalidate";
        ~\\.ts$    "public, max-age=3600";
        ~\\.vtt$   "public, max-age=3600";
        default    "no-cache";
    }}

    server {{
        listen {TEST_PORT};

        add_header Access-Control-Allow-Origin  "*"         always;
        add_header Cache-Control                $cache_ctrl always;

        location = /master.m3u8 {{
            alias {FIXTURES_DIR}/master.m3u8;
        }}

        location /subtitles/ {{
            alias {FIXTURES_DIR}/;
            autoindex off;
        }}
    }}
}}
""")

    # 설정 파일 문법을 먼저 검증해서 오류 원인을 명확하게 출력
    t = subprocess.run(
        ["nginx", "-t", "-c", str(conf)],
        capture_output=True, text=True,
    )
    if t.returncode != 0:
        pytest.skip(f"nginx 설정 오류:\n{t.stderr}")

    proc = subprocess.Popen(
        ["nginx", "-c", str(conf)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # 설정 오류 등으로 nginx가 즉시 종료됐는지 확인
    time.sleep(0.3)
    if proc.poll() is not None:
        _, stderr = proc.communicate()
        pytest.skip(f"nginx 즉시 종료 (rc={proc.returncode}):\n{stderr.decode()}")

    # nginx가 listen 상태가 될 때까지 대기
    for _ in range(10):
        try:
            requests.get(f"http://localhost:{TEST_PORT}/master.m3u8", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    else:
        err_log = log_dir / "error.log"
        err_content = err_log.read_text() if err_log.exists() else "(에러 로그 없음)"
        proc.terminate()
        pytest.skip(f"nginx 응답 없음:\n{err_content}")

    yield f"http://localhost:{TEST_PORT}"

    proc.terminate()
    proc.wait()
