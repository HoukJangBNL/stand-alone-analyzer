import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flake_analysis.api.routes.version import router

def test_version_endpoint():
    """Version endpoint returns flake_core_version + api_version."""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "flake_core_version" in body
    assert body["api_version"] == "v1"
