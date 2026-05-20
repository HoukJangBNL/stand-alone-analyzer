import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flake_analysis.api.errors import (
    AppError,
    ParamsInvalid,
    PrerequisiteMissing,
    app_error_handler,
)


def test_error_envelope_shape():
    """AppError produces correct envelope shape."""
    err = ParamsInvalid(field="quality", reason="must be 1-100")
    envelope = err.to_response()
    assert "error" in envelope
    assert envelope["error"]["code"] == "params_invalid"
    assert envelope["error"]["message"]
    assert envelope["error"]["details"]["field"] == "quality"
    assert "request_id" in envelope["error"]


def test_app_error_handler_integration():
    """FastAPI handler returns 409 with error envelope."""
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/fail")
    async def fail_route():
        raise PrerequisiteMissing(step="background")

    client = TestClient(app)
    resp = client.get("/fail")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "prerequisite_missing"
    assert body["error"]["details"]["step"] == "background"
