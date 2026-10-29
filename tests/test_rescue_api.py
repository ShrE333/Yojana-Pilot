"""Offline Rescue Mode API tests. Use existing curated fixture; no external calls."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.loader import load_schemes
import web.rescue_api as rescue


@pytest.fixture
def client(monkeypatch):
    curated = load_schemes()
    monkeypatch.setattr(rescue, "load_catalog", lambda: curated)
    app = FastAPI()
    app.include_router(rescue.router)
    return TestClient(app)


def test_preflight_flags_mismatch_and_unknowns(client):
    response = client.post("/api/rescue/preflight", json={
        "scheme_id": "pm_kisan",
        "records": {
            "aadhaar": {"name": "Sunil Patil"},
            "land_record": {"name": "Suneel Patil"},
        },
        "facts": {"ekyc_done": False},
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ready"] is False
    assert "ekyc_not_done" in payload["flags"]
    assert payload["disclaimer"]


def test_status_is_explicitly_unverified(client):
    response = client.post("/api/rescue/diagnose", json={
        "scheme_id": "pm_kisan", "status_text": "eKYC pending",
        "facts": {"ekyc_done": False},
    })
    assert response.status_code == 200, response.text
    assert response.json()["government_status_verified"] is False
    assert response.json()["matched_status"]


def test_rejects_sensitive_extra_fields(client):
    response = client.post("/api/rescue/preflight", json={
        "scheme_id": "pm_kisan", "records": {"aadhaar_number": {"name": "Test"}}
    })
    assert response.status_code == 422


def test_missing_scheme(client):
    response = client.post("/api/rescue/preflight", json={"scheme_id": "nonexistent"})
    assert response.status_code == 404


def test_disallows_unverified_discovered_rules(client, monkeypatch):
    monkeypatch.setattr(rescue, "load_catalog", lambda: {
        "new_scheme": {"id": "new_scheme", "origin": "discovered", "verified": False,
                       "actions": {}, "dependencies": []}})
    response = client.post("/api/rescue/preflight", json={"scheme_id": "new_scheme"})
    assert response.status_code == 409
