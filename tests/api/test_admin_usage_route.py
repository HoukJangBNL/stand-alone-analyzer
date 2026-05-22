"""Tests for GET /admin/usage route (W6.4.4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import app
from flake_analysis.db.models import UserRole

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_admin_usage_requires_admin_role(
    monkeypatch, pg_session, sample_user_factory
):
    """GET /admin/usage returns 403 for non-admin users."""
    # Enable dev bypass
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    # Create a member user (not admin)
    await sample_user_factory(
        email="member@test.com", cognito_sub="member-sub", role=UserRole.MEMBER
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/admin/usage")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_usage_returns_all_events(
    monkeypatch, pg_session, sample_user_factory
):
    """GET /admin/usage returns all usage events ordered by ts DESC."""
    # Enable dev bypass
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    # Create admin user
    admin = await sample_user_factory(
        email="admin@test.com", cognito_sub="admin-sub", role=UserRole.ADMIN
    )

    # Create some usage events
    from flake_analysis.api.services.usage import emit
    from flake_analysis.api.auth import User

    domain_admin = User(
        id=admin.id,
        email=admin.email,
        cognito_sub=admin.cognito_sub,
        role=admin.role,
        email_verified=True,
    )

    await emit(pg_session, domain_admin, "login", {"ip": "1.2.3.4"})
    await emit(pg_session, domain_admin, "scan_run", {"step": "thumbnails"})
    await emit(pg_session, domain_admin, "logout")
    await pg_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/admin/usage")
        assert r.status_code == 200
        data = r.json()
        assert "events" in data
        events = data["events"]
        assert len(events) == 3
        # Check ordering (most recent first)
        assert events[0]["kind"] == "logout"
        assert events[1]["kind"] == "scan_run"
        assert events[2]["kind"] == "login"


@pytest.mark.asyncio
async def test_admin_usage_filters_by_user_id(
    monkeypatch, pg_session, sample_user_factory
):
    """GET /admin/usage?user_id=... filters events by user."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    # Create admin and two regular users
    admin = await sample_user_factory(
        email="admin@test.com", cognito_sub="admin-sub", role=UserRole.ADMIN
    )
    user1 = await sample_user_factory(
        email="user1@test.com", cognito_sub="user1-sub"
    )
    user2 = await sample_user_factory(
        email="user2@test.com", cognito_sub="user2-sub"
    )

    # Create events for both users
    from flake_analysis.api.services.usage import emit
    from flake_analysis.api.auth import User

    domain_user1 = User(
        id=user1.id,
        email=user1.email,
        cognito_sub=user1.cognito_sub,
        role=user1.role,
        email_verified=True,
    )
    domain_user2 = User(
        id=user2.id,
        email=user2.email,
        cognito_sub=user2.cognito_sub,
        role=user2.role,
        email_verified=True,
    )

    await emit(pg_session, domain_user1, "login")
    await emit(pg_session, domain_user2, "login")
    await emit(pg_session, domain_user1, "logout")
    await pg_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get(f"/api/v1/admin/usage?user_id={user1.id}")
        assert r.status_code == 200
        data = r.json()
        events = data["events"]
        assert len(events) == 2  # Only user1's events
        assert all(e["user_id"] == str(user1.id) for e in events)


@pytest.mark.asyncio
async def test_admin_usage_filters_by_kind(
    monkeypatch, pg_session, sample_user_factory
):
    """GET /admin/usage?kind=... filters events by kind."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    admin = await sample_user_factory(
        email="admin@test.com", cognito_sub="admin-sub", role=UserRole.ADMIN
    )

    from flake_analysis.api.services.usage import emit
    from flake_analysis.api.auth import User

    domain_admin = User(
        id=admin.id,
        email=admin.email,
        cognito_sub=admin.cognito_sub,
        role=admin.role,
        email_verified=True,
    )

    await emit(pg_session, domain_admin, "login")
    await emit(pg_session, domain_admin, "scan_run")
    await emit(pg_session, domain_admin, "scan_run")
    await emit(pg_session, domain_admin, "logout")
    await pg_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/admin/usage?kind=scan_run")
        assert r.status_code == 200
        data = r.json()
        events = data["events"]
        assert len(events) == 2
        assert all(e["kind"] == "scan_run" for e in events)


@pytest.mark.asyncio
async def test_admin_usage_filters_by_time_range(
    monkeypatch, pg_session, sample_user_factory
):
    """GET /admin/usage?since=...&until=... filters by timestamp."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    admin = await sample_user_factory(
        email="admin@test.com", cognito_sub="admin-sub", role=UserRole.ADMIN
    )

    from flake_analysis.api.services.usage import emit
    from flake_analysis.api.auth import User

    domain_admin = User(
        id=admin.id,
        email=admin.email,
        cognito_sub=admin.cognito_sub,
        role=admin.role,
        email_verified=True,
    )

    # Create events
    await emit(pg_session, domain_admin, "login")
    await emit(pg_session, domain_admin, "scan_run")
    await emit(pg_session, domain_admin, "logout")
    await pg_session.commit()

    # Query with time range that should include all events
    now = datetime.now(timezone.utc)
    since = (now - timedelta(minutes=5)).isoformat()
    until = (now + timedelta(minutes=5)).isoformat()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get(f"/api/v1/admin/usage?since={since}&until={until}")
        assert r.status_code == 200
        data = r.json()
        events = data["events"]
        assert len(events) == 3


@pytest.mark.asyncio
async def test_admin_usage_aggregate_mode(
    monkeypatch, pg_session, sample_user_factory
):
    """GET /admin/usage?aggregate=true returns counts by kind."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    admin = await sample_user_factory(
        email="admin@test.com", cognito_sub="admin-sub", role=UserRole.ADMIN
    )

    from flake_analysis.api.services.usage import emit
    from flake_analysis.api.auth import User

    domain_admin = User(
        id=admin.id,
        email=admin.email,
        cognito_sub=admin.cognito_sub,
        role=admin.role,
        email_verified=True,
    )

    # Create events: 2 login, 3 scan_run, 1 logout
    await emit(pg_session, domain_admin, "login")
    await emit(pg_session, domain_admin, "login")
    await emit(pg_session, domain_admin, "scan_run")
    await emit(pg_session, domain_admin, "scan_run")
    await emit(pg_session, domain_admin, "scan_run")
    await emit(pg_session, domain_admin, "logout")
    await pg_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/admin/usage?aggregate=true")
        assert r.status_code == 200
        data = r.json()
        assert "counts_by_kind" in data
        counts = data["counts_by_kind"]
        assert counts["login"] == 2
        assert counts["scan_run"] == 3
        assert counts["logout"] == 1


@pytest.mark.asyncio
async def test_admin_usage_respects_limit(
    monkeypatch, pg_session, sample_user_factory
):
    """GET /admin/usage?limit=N returns at most N events."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    admin = await sample_user_factory(
        email="admin@test.com", cognito_sub="admin-sub", role=UserRole.ADMIN
    )

    from flake_analysis.api.services.usage import emit
    from flake_analysis.api.auth import User

    domain_admin = User(
        id=admin.id,
        email=admin.email,
        cognito_sub=admin.cognito_sub,
        role=admin.role,
        email_verified=True,
    )

    # Create 5 events
    for _ in range(5):
        await emit(pg_session, domain_admin, "scan_run")
    await pg_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/admin/usage?limit=3")
        assert r.status_code == 200
        data = r.json()
        events = data["events"]
        assert len(events) == 3
