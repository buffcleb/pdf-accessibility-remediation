"""#457: the engine's API won't start without its key, unless told it's local development; with the key, every call
must carry it."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, check_api_key


def test_the_api_refuses_to_start_without_a_key(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "service_api_key", "")
    monkeypatch.setattr(s, "allow_no_api_key", False)
    with pytest.raises(RuntimeError, match="SERVICE_API_KEY isn't set"):
        check_api_key()
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
    monkeypatch.setattr(s, "allow_no_api_key", True)  # local development
    check_api_key()


def test_with_a_key_a_call_without_it_or_with_another_is_refused(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "service_api_key", "the-key")
    monkeypatch.setattr(s, "allow_no_api_key", False)
    with TestClient(app) as c:
        assert c.get("/api/v1/ai/models").status_code == 401
        assert c.get("/api/v1/ai/models", headers={"X-API-Key": "another"}).status_code == 401
        assert c.get("/api/v1/ai/models", headers={"X-API-Key": "the-key"}).status_code != 401
