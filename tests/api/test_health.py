import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flake_analysis.api.routes.health import router

def test_health_endpoint():
    """Health endpoint returns 200 with version and flags."""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "version" in body
    assert "smb_reachable" in body
