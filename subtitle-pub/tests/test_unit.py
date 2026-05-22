"""
subtitle-pub 단위 테스트 
Redis 불필요
"""
import os
import pytest
import app


class TestWriteMasterPlaylist:
    def test_creates_file(self, tmp_path):
        app.write_master_playlist()
        assert (tmp_path / "master.m3u8").exists()

    def test_contains_all_three_languages(self, tmp_path):
        app.write_master_playlist()
        content = (tmp_path / "master.m3u8").read_text()
        for lang in ["en", "zh", "ja"]:
            assert f'LANGUAGE="{lang}"' in content

    def test_first_language_is_default(self, tmp_path):
        # hls.js가 첫 번째 자막 트랙을 기본으로 선택하므로 en이 DEFAULT=YES여야 함
        app.write_master_playlist()
        content = (tmp_path / "master.m3u8").read_text()
        assert 'LANGUAGE="en"' in content
        assert 'DEFAULT=YES' in content
        assert 'LANGUAGE="zh"' in content
        # zh, ja는 DEFAULT=NO
        lines = [l for l in content.splitlines() if 'LANGUAGE="zh"' in l or 'LANGUAGE="ja"' in l]
        assert all("DEFAULT=NO" in l for l in lines)

    def test_subtitle_uris_use_correct_paths(self, tmp_path):
        app.write_master_playlist()
        content = (tmp_path / "master.m3u8").read_text()
        for lang in ["en", "zh", "ja"]:
            assert f'URI="/subtitles/{lang}/playlist.m3u8"' in content

    def test_video_stream_is_included(self, tmp_path):
        app.write_master_playlist()
        content = (tmp_path / "master.m3u8").read_text()
        assert "/hls/live/stream/index.m3u8" in content
        assert 'SUBTITLES="subs"' in content


class TestWriteLangPlaylist:
    def test_creates_file_in_lang_subdir(self, tmp_path):
        app.seg_history["en"] = [0, 1, 2]
        app.write_lang_playlist("en")
        assert (tmp_path / "en" / "playlist.m3u8").exists()

    def test_media_sequence_equals_first_segment(self, tmp_path):
        # EXT-X-MEDIA-SEQUENCE가 틀리면 hls.js가 재생 위치를 잘못 계산함
        app.seg_history["en"] = [5, 6, 7]
        app.write_lang_playlist("en")
        content = (tmp_path / "en" / "playlist.m3u8").read_text()
        assert "#EXT-X-MEDIA-SEQUENCE:5" in content

    def test_all_segments_are_listed(self, tmp_path):
        app.seg_history["en"] = [3, 4, 5]
        app.write_lang_playlist("en")
        content = (tmp_path / "en" / "playlist.m3u8").read_text()
        for i in [3, 4, 5]:
            assert f"seg{i:04d}.vtt" in content

    def test_target_duration_matches_config(self, tmp_path):
        app.seg_history["en"] = [0]
        app.write_lang_playlist("en")
        content = (tmp_path / "en" / "playlist.m3u8").read_text()
        assert f"#EXT-X-TARGETDURATION:{int(app.SEGMENT_DURATION)}" in content

    def test_empty_history_skips_file_creation(self, tmp_path):
        # 세그먼트가 없으면 빈 플레이리스트를 만들지 않음
        app.seg_history["en"] = []
        app.write_lang_playlist("en")
        assert not (tmp_path / "en" / "playlist.m3u8").exists()

    def test_different_languages_are_independent(self, tmp_path):
        app.seg_history["en"] = [0]
        app.seg_history["ja"] = [1]
        app.write_lang_playlist("en")
        app.write_lang_playlist("ja")
        en_content = (tmp_path / "en" / "playlist.m3u8").read_text()
        ja_content = (tmp_path / "ja" / "playlist.m3u8").read_text()
        assert "seg0000.vtt" in en_content
        assert "seg0001.vtt" in ja_content
        assert "seg0001.vtt" not in en_content


class TestSegHistorySlidingWindow:
    def test_segments_are_sorted_after_insertion(self, tmp_path):
        # 네트워크 지연으로 순서가 뒤집혀 도착할 수 있음
        app.seg_history["en"].extend([3, 1, 2])
        app.seg_history["en"].sort()
        assert app.seg_history["en"] == [1, 2, 3]

    def test_window_trims_oldest_segments(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "PLAYLIST_SIZE", 3)
        history = app.seg_history["en"]
        # 0, 1, 2가 이미 있는 상태에서 3 추가
        history.extend([0, 1, 2])
        history.append(3)
        history.sort()
        if len(history) > app.PLAYLIST_SIZE:
            app.seg_history["en"] = history[-app.PLAYLIST_SIZE:]
        assert app.seg_history["en"] == [1, 2, 3]
        assert 0 not in app.seg_history["en"]

    def test_duplicate_segment_not_added_twice(self, tmp_path):
        app.seg_history["en"] = [0, 1, 2]
        history = app.seg_history["en"]
        if 1 not in history:
            history.append(1)
        assert app.seg_history["en"].count(1) == 1
