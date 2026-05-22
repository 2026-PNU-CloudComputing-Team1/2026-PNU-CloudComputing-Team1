import os
import sys

import pytest

# subtitle-pub/app.py를 직접 임포트하기 위해 상위 디렉터리를 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def reset_app_state(tmp_path, monkeypatch):
    """각 테스트 전에 전역 상태와 VTT_DIR을 초기화."""
    import app
    app.seg_history.clear()
    monkeypatch.setattr(app, "VTT_DIR", str(tmp_path))
