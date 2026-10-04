import os
import sys
import tempfile
from pathlib import Path

# Deterministic, offline test environment (set BEFORE importing the app).
_TMP = tempfile.mkdtemp(prefix="helada-test-")
os.environ.update({
    "HELADA_DATA_DIR": _TMP, "HELADA_OFFLINE": "1", "HELADA_TTS": "off", "HELADA_ASR": "mock",
    "HELADA_LLM": "", "HELADA_SCHEDULER": "0", "HELADA_FORCE_STUB": "1", "HELADA_CHANNEL": "sim",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from backend.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, offline=True, tts="off", asr="mock", llm_url="", scheduler=False,
                    channel="sim")


@pytest.fixture
def svc(settings):
    from backend.service import Helada
    return Helada(settings)


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def real_model(monkeypatch):
    """Run the REAL helada_model (a declared dependency) instead of the forced TEMP_STUB, for this test only."""
    import helada_model
    from backend import model_adapter
    monkeypatch.setattr(model_adapter, "_hm", helada_model)
    monkeypatch.setattr(model_adapter, "LAST_ERROR", None)
    return helada_model
