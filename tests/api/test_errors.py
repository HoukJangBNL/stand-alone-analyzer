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


def test_explorer_state_missing_envelope():
    from flake_analysis.api.errors import ExplorerStateMissing
    e = ExplorerStateMissing()
    env = e.to_response()
    assert env["error"]["code"] == "explorer_state_missing"
    assert e.status_code == 404


def test_thumbnail_missing_envelope():
    from flake_analysis.api.errors import ThumbnailMissing
    e = ThumbnailMissing(lod=0, stem="ix003_iy017")
    env = e.to_response()
    assert env["error"]["code"] == "thumbnail_missing"
    assert env["error"]["details"] == {"lod": 0, "stem": "ix003_iy017"}
    assert e.status_code == 404


def test_raw_image_missing_envelope():
    from flake_analysis.api.errors import RawImageMissing
    e = RawImageMissing(filename="ix003_iy017.png")
    env = e.to_response()
    assert env["error"]["code"] == "raw_image_missing"
    assert e.status_code == 404


def test_flake_not_found_envelope():
    from flake_analysis.api.errors import FlakeNotFound
    e = FlakeNotFound(flake_id=99999)
    env = e.to_response()
    assert env["error"]["code"] == "flake_not_found"
    assert env["error"]["details"] == {"flake_id": 99999}
    assert e.status_code == 404


def test_forbidden_error_envelope_shape():
    from flake_analysis.api.errors import Forbidden

    err = Forbidden(action="finalize", scan_id=42)
    payload = err.to_response()
    assert err.status_code == 403
    assert err.code == "forbidden"
    assert payload["error"]["code"] == "forbidden"
    assert payload["error"]["details"] == {"action": "finalize", "scan_id": 42}
    assert "request_id" in payload["error"]
