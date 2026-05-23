"""Shared pytest fixtures for tests/api/."""
import os
import time

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64

# Re-export PG fixtures from tests/db/conftest.py so pytest.mark.pg tests
# under tests/api/ can use them. pytest only auto-discovers conftest along
# the rootdir->test_file path; tests/db/conftest.py is on a sibling branch.
from tests.db.conftest import (  # noqa: F401
    pg_session,
    pg_url,
    sample_analysis_factory,
    sample_user_factory,
)


@pytest_asyncio.fixture()
async def sample_project_factory(pg_session, sample_user_factory):
    """Insert a Project owned by the given user and return it.

    The W10-C route tests need to scope multiple projects to the same user
    (e.g. "list scans for project A but not project B"). Callers must pass
    `owner` explicitly so ownership is deterministic.
    """
    from flake_analysis.db.models import Project

    counter = {"n": 0}

    async def _make(*, owner) -> "Project":
        counter["n"] += 1
        p = Project(name=f"sample-project-{counter['n']}", owner_id=owner.id)
        pg_session.add(p)
        await pg_session.flush()
        await pg_session.refresh(p)
        return p

    return _make


@pytest_asyncio.fixture()
async def active_material(pg_session):
    """Materials are seeded by alembic but tests may run on an empty DB.

    Returns the canonical "graphene" Material row, inserting it on demand.
    Idempotent: returns existing row when present (e.g. seeded via 0001).
    """
    from flake_analysis.db.models import Material

    existing = await pg_session.get(Material, "graphene")
    if existing is not None:
        return existing
    m = Material(name="graphene")
    pg_session.add(m)
    await pg_session.flush()
    await pg_session.refresh(m)
    return m


@pytest_asyncio.fixture()
async def active_project(pg_session, sample_user_factory):
    """Insert a fresh `projects` row owned by an auto-created dev user.

    Returns the Project ORM instance. Use `active_scan` instead when a Scan
    is needed — `active_scan` depends on `active_project` so a single
    Project row is shared.
    """
    from flake_analysis.db.models import Project

    user = await sample_user_factory()
    p = Project(name="w10-active-project", owner_id=user.id)
    pg_session.add(p)
    await pg_session.flush()
    await pg_session.refresh(p)
    return p


@pytest_asyncio.fixture()
async def active_scan(pg_session, active_project, active_material):
    """Insert a Scan under `active_project`. Returns the Scan.

    Replaces the legacy `_active_project` setter — every test that used to
    mutate the global now injects `active_scan` and reads `.project_id` /
    `.id` off the returned ORM instance.
    """
    from flake_analysis.db.models import Scan

    s = Scan(
        name="w10-active-scan",
        material=active_material.name,
        project_id=active_project.id,
        image_count=4,
    )
    pg_session.add(s)
    await pg_session.flush()
    await pg_session.refresh(s)
    return s


@pytest_asyncio.fixture()
async def sample_scan_factory(pg_session, sample_user_factory):
    """Insert a Scan and return it.

    No-arg call (`await sample_scan_factory()`) auto-creates a fresh User +
    Project so existing W10-B tests keep working. Pass `project=` to scope
    the scan to an existing project (W10-C listing tests). Pass `name=` to
    override the auto-generated scan name.

    Mirrors the W6 `sample_user_factory` pattern; W10-A made `scans.project_id`
    a FK->projects.id RESTRICT NOT NULL so we must construct a real Project
    when none is supplied.
    """
    from flake_analysis.db.models import Project, Scan

    counter = {"n": 0}

    async def _make(*, project=None, name=None) -> "Scan":
        counter["n"] += 1
        suffix = counter["n"]
        if project is None:
            u = await sample_user_factory()
            project = Project(name=f"test-project-{suffix}", owner_id=u.id)
            pg_session.add(project)
            await pg_session.flush()
            await pg_session.refresh(project)
        s_name = name or f"test-scan-{suffix}"
        s = Scan(name=s_name, material="graphene", project_id=project.id)
        pg_session.add(s)
        await pg_session.flush()
        await pg_session.refresh(s)
        return s

    return _make


@pytest.fixture(autouse=True)
def _enable_dev_bypass(monkeypatch):
    """Enable dev bypass for all API tests unless explicitly disabled.

    Tests that need real Cognito auth can disable this by passing
    `_enable_dev_bypass=False` or by using the signed_token fixture.
    """
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("SAA_ENV", "dev")


@pytest.fixture
def rsa_key():
    """Generate an RSA keypair for test token signing."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def signed_token(rsa_key, monkeypatch):
    """Generate a valid Cognito ID token signed with test key.

    Sets up the JWKS cache and env vars so verify_id_token will accept it.
    """
    from flake_analysis.api.auth.tokens import _JwksCache

    # Build test JWKS
    pub = rsa_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-kid",
        "use": "sig",
        "alg": "RS256",
        "n": long_to_base64(pub.n).decode(),
        "e": long_to_base64(pub.e).decode(),
    }

    # Prime the cache
    cache = _JwksCache()
    cache.set([jwk])
    monkeypatch.setattr("flake_analysis.api.auth.tokens._jwks_cache", cache)

    # Set env vars
    monkeypatch.setenv("SAA_COGNITO_AUDIENCE", "test-client-id")
    monkeypatch.setenv("SAA_COGNITO_ISSUER", "https://test.example/pool")

    # Sign a valid token
    pem = rsa_key.private_bytes(
        encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.PEM,
        format=__import__("cryptography").hazmat.primitives.serialization.PrivateFormat.PKCS8,
        encryption_algorithm=__import__("cryptography").hazmat.primitives.serialization.NoEncryption(),
    )

    now = int(time.time())
    claims = {
        "sub": "test-user-1",
        "aud": "test-client-id",
        "iss": "https://test.example/pool",
        "token_use": "id",
        "email": "test@example.com",
        "email_verified": True,
        "exp": now + 3600,
        "iat": now,
    }

    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test-kid"})
