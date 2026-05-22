"""Test role-based guards (require_role, require_project_role)."""
import pytest
from uuid import uuid4
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.guards import require_role, require_project_role
from flake_analysis.db.models import UserRole, ProjectRole


def make_app_with_override(override_user: User | None = None) -> FastAPI:
    """Create test app with optional user override."""
    app = FastAPI()

    if override_user:

        async def mock_current_user() -> User:
            return override_user

        app.dependency_overrides[get_current_user] = mock_current_user

    @app.get("/test/operator")
    async def operator_route(user: User = Depends(require_role(UserRole.OPERATOR))):
        return {"ok": True, "user_email": user.email}

    @app.get("/test/admin")
    async def admin_route(user: User = Depends(require_role(UserRole.ADMIN))):
        return {"ok": True, "user_email": user.email}

    @app.get("/test/projects/{project_id}/viewer")
    async def viewer_route(
        project_id: str,
        user: User = Depends(require_project_role("project_id", ProjectRole.VIEWER)),
    ):
        return {"ok": True, "project_id": project_id}

    @app.get("/test/projects/{project_id}/editor")
    async def editor_route(
        project_id: str,
        user: User = Depends(require_project_role("project_id", ProjectRole.EDITOR)),
    ):
        return {"ok": True, "project_id": project_id}

    return app


# ========== require_role tests ==========


def test_require_role_operator_allows_operator() -> None:
    """Operator user can access operator-gated route."""
    operator = User(
        id=uuid4(),
        email="operator@example.com",
        role=UserRole.OPERATOR,
        email_verified=True,
        cognito_sub="test:operator",
    )
    app = make_app_with_override(operator)
    client = TestClient(app)
    resp = client.get("/test/operator")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_require_role_operator_rejects_member() -> None:
    """Member cannot access operator-gated route."""
    member = User(
        id=uuid4(),
        email="member@example.com",
        role=UserRole.MEMBER,
        email_verified=True,
        cognito_sub="test:member",
    )
    app = make_app_with_override(member)
    client = TestClient(app)
    resp = client.get("/test/operator")
    assert resp.status_code == 403
    assert "Insufficient privilege" in resp.json()["detail"]


def test_require_role_admin_rejects_operator() -> None:
    """Operator cannot access admin-gated route."""
    operator = User(
        id=uuid4(),
        email="operator@example.com",
        role=UserRole.OPERATOR,
        email_verified=True,
        cognito_sub="test:operator",
    )
    app = make_app_with_override(operator)
    client = TestClient(app)
    resp = client.get("/test/admin")
    assert resp.status_code == 403


# ========== require_project_role tests ==========
# Note: Full project-role tests require projects table (W2.x).
# These tests verify the guard logic with mocked DB responses.


def test_require_project_role_operator_always_editor() -> None:
    """Operator always gets EDITOR regardless of ACL (via acl resolver)."""
    # This test verifies the integration: operator → resolver → EDITOR
    # The actual DB query will happen but operator short-circuits in resolver
    operator = User(
        id=uuid4(),
        email="operator@example.com",
        role=UserRole.OPERATOR,
        email_verified=True,
        cognito_sub="test:operator",
    )

    app = make_app_with_override(operator)

    # Need to override get_db_session to avoid real DB
    from sqlalchemy.ext.asyncio import AsyncSession
    from flake_analysis.api.deps import get_db_session

    async def mock_db():
        class MockSession:
            async def execute(self, stmt):
                class MockResult:
                    def scalar_one_or_none(self):
                        return None

                return MockResult()

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.get("/test/projects/test-proj/editor")
    # Operator gets EDITOR via resolver, so 200
    assert resp.status_code == 200


def test_require_project_role_admin_always_editor() -> None:
    """Admin always gets EDITOR."""
    admin = User(
        id=uuid4(),
        email="admin@example.com",
        role=UserRole.ADMIN,
        email_verified=True,
        cognito_sub="test:admin",
    )

    app = make_app_with_override(admin)

    from flake_analysis.api.deps import get_db_session

    async def mock_db():
        class MockSession:
            async def execute(self, stmt):
                class MockResult:
                    def scalar_one_or_none(self):
                        return None

                return MockResult()

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.get("/test/projects/test-proj/viewer")
    assert resp.status_code == 200


def test_require_project_role_member_no_acl_403() -> None:
    """Member without ACL or ownership → 403."""
    member = User(
        id=uuid4(),
        email="member@example.com",
        role=UserRole.MEMBER,
        email_verified=True,
        cognito_sub="test:member",
    )

    app = make_app_with_override(member)

    from flake_analysis.api.deps import get_db_session

    async def mock_db():
        class MockSession:
            async def execute(self, stmt):
                class MockResult:
                    def scalar_one_or_none(self):
                        # No ownership, no ACL
                        return None

                return MockResult()

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.get("/test/projects/test-proj/viewer")
    assert resp.status_code == 403


def test_require_project_role_member_with_viewer_acl_allows_viewer() -> None:
    """Member with VIEWER ACL can access viewer-gated route."""
    member = User(
        id=uuid4(),
        email="member@example.com",
        role=UserRole.MEMBER,
        email_verified=True,
        cognito_sub="test:member",
    )

    app = make_app_with_override(member)

    from flake_analysis.api.deps import get_db_session

    call_count = {"count": 0}

    async def mock_db():
        class MockSession:
            async def execute(self, stmt):
                class MockResult:
                    def scalar_one_or_none(self):
                        # First call: ownership check → None
                        # Second call: ACL query → VIEWER
                        call_count["count"] += 1
                        if call_count["count"] == 1:
                            return None  # Not owner
                        else:
                            return ProjectRole.VIEWER  # Has VIEWER ACL

                return MockResult()

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.get("/test/projects/test-proj/viewer")
    assert resp.status_code == 200


def test_require_project_role_member_with_viewer_acl_rejects_editor() -> None:
    """Member with only VIEWER ACL cannot access editor-gated route."""
    member = User(
        id=uuid4(),
        email="member@example.com",
        role=UserRole.MEMBER,
        email_verified=True,
        cognito_sub="test:member",
    )

    app = make_app_with_override(member)

    from flake_analysis.api.deps import get_db_session

    call_count = {"count": 0}

    async def mock_db():
        class MockSession:
            async def execute(self, stmt):
                class MockResult:
                    def scalar_one_or_none(self):
                        call_count["count"] += 1
                        if call_count["count"] == 1:
                            return None  # Not owner
                        else:
                            return ProjectRole.VIEWER  # Has VIEWER ACL

                return MockResult()

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.get("/test/projects/test-proj/editor")
    assert resp.status_code == 403
