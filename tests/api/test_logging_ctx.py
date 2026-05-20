import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flake_analysis.api.logging_ctx import (
    get_request_id,
    set_request_id,
    RequestIdMiddleware,
)

def test_request_id_contextvar():
    """ContextVar can be set and retrieved."""
    rid = set_request_id("test-123")
    assert rid == "test-123"
    assert get_request_id() == "test-123"

def test_request_id_middleware():
    """Middleware injects UUID4 request_id on every request."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/test")
    async def test_route():
        return {"request_id": get_request_id()}

    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 200
    rid = resp.json()["request_id"]
    assert rid
    assert len(rid) == 36
