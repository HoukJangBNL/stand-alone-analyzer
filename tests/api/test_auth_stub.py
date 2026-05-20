import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from flake_analysis.api.auth import User, get_current_user

def test_user_dataclass_shape():
    """User has id and roles."""
    u = User(id="test", roles=("owner",))
    assert u.id == "test"
    assert "owner" in u.roles

def test_get_current_user_stub():
    """Stub returns local user with owner role."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user: User = Depends(get_current_user)):
        return {"id": user.id, "roles": list(user.roles)}

    client = TestClient(app)
    resp = client.get("/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "local"
    assert "owner" in body["roles"]
