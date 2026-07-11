import os

os.environ["API_TOKEN"] = "test-token"

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_public():
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "version" in body


def test_me_rejects_missing_token():
    assert client.get("/api/me").status_code == 401


def test_me_rejects_wrong_token():
    res = client.get("/api/me", headers={"Authorization": "Bearer wrong"})
    assert res.status_code == 401


def test_me_accepts_valid_token():
    res = client.get("/api/me", headers={"Authorization": "Bearer test-token"})
    assert res.status_code == 200
    assert res.json()["user"]["id"] == 1


def test_me_rejects_when_no_token_configured(monkeypatch):
    # An unset API_TOKEN must fail closed, never open.
    monkeypatch.setenv("API_TOKEN", "")
    res = client.get("/api/me", headers={"Authorization": "Bearer "})
    assert res.status_code == 401
