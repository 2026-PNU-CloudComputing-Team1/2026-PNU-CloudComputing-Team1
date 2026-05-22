"""
nginx-origin 서비스 테스트 — 로컬 nginx 서버 필요
conftest.py의 nginx_url fixture가 fixtures/ 디렉터리를 마운트한 nginx를 8090에 구동

실행:
  cd nginx-origin
  pytest tests/test_service.py -v
"""
import requests
import pytest


class TestCacheControlHeaders:
    def test_vtt_file_is_cacheable(self, nginx_url):
        # .vtt는 한 번 쓰면 바뀌지 않으므로 엣지 캐시가 가능해야 함
        r = requests.get(f"{nginx_url}/subtitles/en/seg0000.vtt")
        assert "max-age=3600" in r.headers.get("Cache-Control", "")

    def test_lang_playlist_is_not_cached(self, nginx_url):
        # 언어별 playlist.m3u8은 세그먼트마다 갱신되므로 캐시 금지
        r = requests.get(f"{nginx_url}/subtitles/en/playlist.m3u8")
        cc = r.headers.get("Cache-Control", "")
        assert "no-cache" in cc or "no-store" in cc

    def test_master_m3u8_is_not_cached(self, nginx_url):
        r = requests.get(f"{nginx_url}/master.m3u8")
        cc = r.headers.get("Cache-Control", "")
        assert "no-cache" in cc or "no-store" in cc

    def test_nonexistent_vtt_still_has_cache_header(self, nginx_url):
        # always 플래그 확인: 404 응답에도 Cache-Control이 있어야 함
        r = requests.get(f"{nginx_url}/subtitles/en/seg9999.vtt")
        assert r.status_code == 404
        assert "max-age=3600" in r.headers.get("Cache-Control", "")


class TestCORSHeaders:
    def test_cors_origin_is_wildcard(self, nginx_url):
        r = requests.get(f"{nginx_url}/master.m3u8")
        assert r.headers.get("Access-Control-Allow-Origin") == "*"

    def test_cors_present_on_404(self, nginx_url):
        # always 플래그 확인: 오류 응답에도 CORS 헤더 필요
        r = requests.get(f"{nginx_url}/subtitles/en/seg9999.vtt")
        assert r.headers.get("Access-Control-Allow-Origin") == "*"


class TestFileServing:
    def test_master_m3u8_returns_200(self, nginx_url):
        r = requests.get(f"{nginx_url}/master.m3u8")
        assert r.status_code == 200

    def test_master_m3u8_contains_subtitle_tracks(self, nginx_url):
        r = requests.get(f"{nginx_url}/master.m3u8")
        for lang in ["en", "zh", "ja"]:
            assert f'LANGUAGE="{lang}"' in r.text

    def test_vtt_file_returns_200(self, nginx_url):
        r = requests.get(f"{nginx_url}/subtitles/en/seg0000.vtt")
        assert r.status_code == 200

    def test_vtt_file_content_is_valid_webvtt(self, nginx_url):
        r = requests.get(f"{nginx_url}/subtitles/en/seg0000.vtt")
        assert r.text.startswith("WEBVTT")

    def test_lang_playlist_returns_200(self, nginx_url):
        r = requests.get(f"{nginx_url}/subtitles/en/playlist.m3u8")
        assert r.status_code == 200

    def test_lang_playlist_contains_segment(self, nginx_url):
        r = requests.get(f"{nginx_url}/subtitles/en/playlist.m3u8")
        assert "seg0000.vtt" in r.text


class TestMimeTypes:
    def test_vtt_content_type(self, nginx_url):
        r = requests.get(f"{nginx_url}/subtitles/en/seg0000.vtt")
        assert "text/vtt" in r.headers.get("Content-Type", "")

    def test_m3u8_content_type(self, nginx_url):
        r = requests.get(f"{nginx_url}/master.m3u8")
        ct = r.headers.get("Content-Type", "")
        assert "mpegurl" in ct or "m3u8" in ct
