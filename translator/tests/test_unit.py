"""
translator 단위 테스트 — 외부 서비스(Redis, GCP) 불필요
"""
import os
import pytest
import app


class TestPtsToVttTs:
    def test_zero(self):
        assert app.pts_to_vtt_ts(0.0) == "00:00:00.000"

    def test_seconds_only(self):
        assert app.pts_to_vtt_ts(5.5) == "00:00:05.500"

    def test_minutes(self):
        assert app.pts_to_vtt_ts(90.0) == "00:01:30.000"

    def test_hours(self):
        assert app.pts_to_vtt_ts(3661.0) == "01:01:01.000"

    def test_milliseconds_precision(self):
        assert app.pts_to_vtt_ts(1.234) == "00:00:01.234"


class TestGoogleLang:
    def test_zh_maps_to_zh_cn(self):
        # Google Cloud API는 "zh"를 인식 못함, "zh-CN"으로 변환해야 간체 중국어
        assert app._google_lang("zh") == "zh-CN"

    def test_en_unchanged(self):
        assert app._google_lang("en") == "en"

    def test_ja_unchanged(self):
        assert app._google_lang("ja") == "ja"


class TestWriteVtt:
    def test_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        path = app.write_vtt("en", 0, 0.0, 2.0, "Hello")
        assert os.path.exists(path)

    def test_path_format(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        path = app.write_vtt("en", 3, 6.0, 8.0, "Hello")
        assert path.endswith("en/seg0003.vtt")

    def test_webvtt_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        path = app.write_vtt("en", 0, 0.0, 2.0, "Hello")
        assert open(path).read().startswith("WEBVTT")

    def test_timestamp_line(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        path = app.write_vtt("en", 0, 0.0, 2.0, "Hello")
        content = open(path).read()
        assert "00:00:00.000 --> 00:00:02.000" in content

    def test_text_in_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        path = app.write_vtt("ja", 1, 2.0, 4.0, "こんにちは")
        assert "こんにちは" in open(path).read()

    def test_creates_lang_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        app.write_vtt("zh", 0, 0.0, 2.0, "你好")
        assert (tmp_path / "zh").is_dir()

    def test_equal_pts_gets_minimum_duration(self, tmp_path, monkeypatch):
        # end_pts == start_pts이면 0.1초 최소 간격으로 보정
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        path = app.write_vtt("en", 0, 5.0, 5.0, "Hi")
        content = open(path).read()
        assert "00:00:05.000 --> 00:00:05.100" in content

    def test_high_segment_num_zero_padded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
        path = app.write_vtt("en", 42, 84.0, 86.0, "text")
        assert "seg0042.vtt" in path
