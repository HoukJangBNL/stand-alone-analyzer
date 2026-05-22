"""Test admin routes (role management, ACL, deactivation)."""
import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from flake_analysis.api.auth import User
from flake_analysis.api.main import create_app
from flake_analysis.db.models import UserRole, ProjectRole


@pytest.fixture
def admin_user() -> User:
    """Admin user fixture."""
    return User(
        id=uuid4(),
        email="admin@example.com",
        role=UserRole.ADMIN,
        email_verified=True,
        cognito_sub="admin:test",
    )


@pytest.fixture
def member_user() -> User:
    """Member user fixture."""
    return User(
        id=uuid4(),
        email="member@example.com",
        role=UserRole.MEMBER,
        email_verified=True,
        cognito_sub="member:test",
    )


# ========== Role management ==========


@pytest.mark.pg
@pytest.mark.asyncio
async def test_admin_change_role_happy_path(pg_session, admin_user) -> None:
    """Admin can change another user's role."""
    from flake_analysis.db.models.user import User as UserModel
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    # Create target user
    target_id = uuid4()
    target_user = UserModel(
        id=target_id,
        email="target@example.com",
        cognito_sub="target:test",
        role=UserRole.MEMBER,
    )
    pg_session.add(target_user)
    await pg_session.commit()

    # Create app with admin override
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_db_session] = lambda: pg_session

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/admin/users/{target_id}/role",
        json={"role": "operator"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"

    # Verify DB update
    await pg_session.refresh(target_user)
    assert target_user.role == UserRole.OPERATOR


@pytest.mark.asyncio
async def test_admin_change_role_rejects_non_admin(member_user) -> None:
    """Non-admin cannot change roles."""
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: member_user

    # Mock DB
    async def mock_db():
        class MockSession:
            pass

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/admin/users/{uuid4()}/role",
        json={"role": "admin"},
    )
    assert resp.status_code == 403


def test_admin_change_role_rejects_self_demotion(admin_user) -> None:
    """Admin cannot demote themselves below admin."""
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin_user

    # Mock DB
    async def mock_db():
        class MockSession:
            pass

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/admin/users/{admin_user.id}/role",
        json={"role": "member"},
    )
    assert resp.status_code == 400
    assert "Cannot demote yourself" in resp.json()["detail"]


# ========== Deactivation ==========


@pytest.mark.pg
@pytest.mark.asyncio
async def test_admin_deactivate_user(pg_session, admin_user) -> None:
    """Admin can deactivate another user."""
    from flake_analysis.db.models.user import User as UserModel
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    # Create target user
    target_id = uuid4()
    target_user = UserModel(
        id=target_id,
        email="target@example.com",
        cognito_sub="target:test",
        role=UserRole.MEMBER,
    )
    pg_session.add(target_user)
    await pg_session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_db_session] = lambda: pg_session

    client = TestClient(app)
    resp = client.post(f"/api/v1/admin/users/{target_id}/deactivate")
    assert resp.status_code == 200

    # Verify deactivated_at is set
    await pg_session.refresh(target_user)
    assert target_user.deactivated_at is not None


def test_admin_deactivate_rejects_self(admin_user) -> None:
    """Admin cannot deactivate themselves."""
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin_user

    async def mock_db():
        class MockSession:
            pass

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.post(f"/api/v1/admin/users/{admin_user.id}/deactivate")
    assert resp.status_code == 400
    assert "Cannot deactivate yourself" in resp.json()["detail"]


@pytest.mark.pg
@pytest.mark.asyncio
async def test_admin_reactivate_user(pg_session, admin_user) -> None:
    """Admin can reactivate a deactivated user."""
    from datetime import datetime, timezone
    from flake_analysis.db.models.user import User as UserModel
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    # Create deactivated user
    target_id = uuid4()
    target_user = UserModel(
        id=target_id,
        email="target@example.com",
        cognito_sub="target:test",
        role=UserRole.MEMBER,
        deactivated_at=datetime.now(timezone.utc),
    )
    pg_session.add(target_user)
    await pg_session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_db_session] = lambda: pg_session

    client = TestClient(app)
    resp = client.post(f"/api/v1/admin/users/{target_id}/reactivate")
    assert resp.status_code == 200

    # Verify deactivated_at is cleared
    await pg_session.refresh(target_user)
    assert target_user.deactivated_at is None


# ========== ACL management ==========


@pytest.mark.pg
@pytest.mark.asyncio
async def test_admin_grant_acl(pg_session, admin_user) -> None:
    """Admin can grant project ACL."""
    from flake_analysis.db.models.user import User as UserModel
    from flake_analysis.db.models.auth import ProjectUser
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session
    from sqlalchemy import select

    # Create target user
    target_id = uuid4()
    target_user = UserModel(
        id=target_id,
        email="target@example.com",
        cognito_sub="target:test",
        role=UserRole.MEMBER,
    )
    pg_session.add(target_user)
    await pg_session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_db_session] = lambda: pg_session

    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/projects/test-proj/acl",
        json={"user_id": str(target_id), "project_role": "viewer"},
    )
    assert resp.status_code == 200

    # Verify ACL row created
    stmt = (
        select(ProjectUser)
        .where(ProjectUser.project_id == "test-proj")
        .where(ProjectUser.user_id == target_id)
    )
    result = await pg_session.execute(stmt)
    acl_row = result.scalar_one_or_none()
    assert acl_row is not None
    assert acl_row.project_role == ProjectRole.VIEWER


@pytest.mark.asyncio
async def test_admin_grant_acl_rejects_non_admin(member_user) -> None:
    """Non-admin cannot grant ACL."""
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: member_user

    async def mock_db():
        class MockSession:
            pass

        return MockSession()

    app.dependency_overrides[get_db_session] = mock_db

    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/projects/test-proj/acl",
        json={"user_id": str(uuid4()), "project_role": "editor"},
    )
    assert resp.status_code == 403


@pytest.mark.pg
@pytest.mark.asyncio
async def test_admin_revoke_acl(pg_session, admin_user) -> None:
    """Admin can revoke project ACL."""
    from flake_analysis.db.models.user import User as UserModel
    from flake_analysis.db.models.auth import ProjectUser
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session
    from sqlalchemy import select

    # Create target user with ACL
    target_id = uuid4()
    target_user = UserModel(
        id=target_id,
        email="target@example.com",
        cognito_sub="target:test",
        role=UserRole.MEMBER,
    )
    pg_session.add(target_user)
    await pg_session.flush()

    acl_row = ProjectUser(
        project_id="test-proj",
        user_id=target_id,
        project_role=ProjectRole.VIEWER,
    )
    pg_session.add(acl_row)
    await pg_session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_db_session] = lambda: pg_session

    client = TestClient(app)
    resp = client.delete(f"/api/v1/admin/projects/test-proj/acl/{target_id}")
    assert resp.status_code == 200

    # Verify ACL row deleted
    stmt = (
        select(ProjectUser)
        .where(ProjectUser.project_id == "test-proj")
        .where(ProjectUser.user_id == target_id)
    )
    result = await pg_session.execute(stmt)
    assert result.scalar_one_or_none() is None
