# W5-B2 — Upload Flow API (presigned PUT + complete + finalize)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the second half of the W5 image upload API on top of W5-B1 (materials + scan create). Adds four endpoints: presigned S3 PUT URLs with SHA256 enforcement, per-image complete (promotes upload_item → canonical images row), scan finalize (count check + usage event), and scan detail GET. Browser computes SHA256, server signs it into the presigned PUT so S3 rejects mismatched bytes (BadDigest 400). All routes require `get_current_user` (dev-bypass + Cognito both supported).

**Architecture:** **Depends on W5-B1 merged on main.** W5-B1 created `src/flake_analysis/api/schemas/upload.py`, `src/flake_analysis/api/services/upload_service.py`, `src/flake_analysis/api/routes/scans.py` (with `POST /projects/{pid}/scans`), and `src/flake_analysis/api/routes/materials.py`. This plan **appends** to those files (it never overwrites them) and creates one new file: `src/flake_analysis/api/services/s3_presign.py`. The W5-A migration is already applied on `saa_test` (verified 2026-05-22 — `materials` populated with 5 seed rows, `scans.material` NOT NULL+FK, `images.grid_ix/grid_iy` NOT NULL+UNIQUE). Routes layer over the existing `upload_sessions`/`upload_items` machinery: per-scan upload session is created on first presign, one `upload_items` row per presigned file (status `pending` → `uploaded`), and the canonical `images` row is inserted only at the `complete` step. This avoids inventing a new "pending images" status column.

**Tech Stack:** FastAPI 0.110+, pydantic v2, SQLAlchemy 2.x async ORM, PostgreSQL 16, boto3 (presigned PUT), `moto[s3]` for S3 mocking in tests, pytest-asyncio strict + `pytest.mark.pg`, httpx ASGITransport.

---

## Naming Decisions (locked 2026-05-22)

- Single bucket `qpress-uploads` (us-east-2) with `dev/` and `prod/` prefixes (W5-D D1). API reads from env: `SAA_S3_BUCKET`, `SAA_S3_PREFIX`.
- S3 key layout: `{prefix}scans/{scan_id}/images/{sha256}.{ext}` — content-addressed, dedupe-friendly. (Note: `SAA_S3_PREFIX` already includes the trailing slash, e.g. `dev/`.)
- `images.s3_uri` stores the **full** `s3://bucket/key` URI (W5-A locked).
- Client SHA256 is hex (browser Web Crypto outputs hex); `x-amz-checksum-sha256` is base64 of the raw 32-byte digest. The presign endpoint converts `bytes.fromhex(sha256_hex)` → base64 server-side.
- Presigned PUT `ExpiresIn=300` (5 min). The `ChecksumSHA256` is signed into the URL so S3 rejects mismatched bytes server-side.
- ix/iy: 0-based, (0,0) = top-left (W5-A locked).
- Auth: `from flake_analysis.api.auth import User, get_current_user` — same import that all v7 routes already use.
- Routes scoped: `/api/v1/scans/{scan_id}/...` (everything in this plan). `POST /projects/{pid}/scans` (create) shipped in W5-B1.
- **`project_id` is path-only** (locked in W5-B1) — no `scans.project_id` column. The endpoint MAY emit `usage_events` rows; W5-B2's finalize emits one of kind `scan_uploaded`.

---

## File Structure

- **Append**: `src/flake_analysis/api/schemas/upload.py` (created in W5-B1) — add presign/complete/finalize/scan-detail models.
- **Append**: `src/flake_analysis/api/services/upload_service.py` (created in W5-B1) — add upload-session and upload-item lifecycle helpers.
- **Append**: `src/flake_analysis/api/routes/scans.py` (created in W5-B1) — add presign, complete, finalize, GET handlers.
- **Create**: `src/flake_analysis/api/services/s3_presign.py` — boto3 wrapper, SHA256 hex→base64, presign generator, head_object.
- **Modify**: `pyproject.toml` — add `boto3>=1.34` to runtime deps; add `moto[s3]>=5.0` to dev group.
- **Create**: `tests/api/test_s3_presign_service.py` — moto-backed unit tests.
- **Append**: `tests/api/test_upload_schemas.py` (created in W5-B1) — add presign/complete/finalize schema tests.
- **Create**: `tests/api/test_scans_presign.py` — PG + httpx + moto.
- **Create**: `tests/api/test_scans_complete.py` — PG + httpx + moto.
- **Create**: `tests/api/test_scans_finalize.py` — PG + httpx + moto.

All tests use the `get_db_session` dependency override pattern from `tests/api/test_admin_usage_route.py` — read that file FIRST and mirror the override pattern in every test.

---

## Verification Env Block

All test runs MUST use this exact prefix:

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
```

(`AWS_*` are dummies for moto; the real prod creds come from EC2 instance role and are not used here.)

Migration assumed at head `0003_w5a_materials_uploads` (W5-A complete). W5-B1 must already be merged on main.

---

## Step 0.1 — Verify W5-B1 dependency

Before starting any task in this plan, confirm W5-B1 is merged.

- [ ] **Run from repo root:**

```bash
git log --oneline | grep -E "feat\(api\): (materials|scan create|W5-B1)"
```

Expected: at least two commits matching (one for materials routes, one for scan create). If empty: HALT, do not proceed — W5-B1 must be merged first.

- [ ] **Confirm the W5-B1 source files exist (PM-side sanity check):**

```bash
ls src/flake_analysis/api/schemas/upload.py \
   src/flake_analysis/api/services/upload_service.py \
   src/flake_analysis/api/routes/materials.py \
   src/flake_analysis/api/routes/scans.py
```

Expected: all four files present. If any are missing: HALT.

---

## Task 1 — Wire deps, extend schemas, build presign service

**Files:**
- Modify: `pyproject.toml`
- Append: `src/flake_analysis/api/schemas/upload.py`
- Append: `src/flake_analysis/api/services/upload_service.py`
- Create: `src/flake_analysis/api/services/s3_presign.py`
- Append: `tests/api/test_upload_schemas.py`
- Create: `tests/api/test_s3_presign_service.py`

**Why:** Land the leaf-level building blocks before any route exists. Schemas + presign service can be unit-tested without hitting the DB or HTTP layer.

### Step 1.1: Add boto3 + moto deps

- [ ] **In `pyproject.toml`, add to `[project] dependencies` list:**

```toml
"boto3>=1.34",
```

- [ ] **In `pyproject.toml`, add to `[project.optional-dependencies] dev` list:**

```toml
"moto[s3]>=5.0",
```

- [ ] **Run:**

```
uv sync --extra dev
```

Expected: lock file regenerates; `boto3` and `moto` are installed.

### Step 1.2: Append failing schema tests for presign/complete/finalize

- [ ] **Append to `tests/api/test_upload_schemas.py` (created in W5-B1):**

```python
from flake_analysis.api.schemas.upload import (
    PresignRequest,
    PresignResponse,
    CompleteRequest,
    FinalizeResponse,
)


def test_presign_request_validates_sha256_hex():
    good = PresignRequest(
        filename="t.tif", sha256="a" * 64, grid_ix=0, grid_iy=0, size_bytes=1024,
    )
    assert good.sha256 == "a" * 64
    with pytest.raises(ValidationError):
        PresignRequest(filename="t.tif", sha256="zz", grid_ix=0, grid_iy=0, size_bytes=1024)


def test_presign_request_rejects_negative_grid():
    with pytest.raises(ValidationError):
        PresignRequest(
            filename="t.tif", sha256="a" * 64, grid_ix=-1, grid_iy=0, size_bytes=1024,
        )


def test_presign_response_round_trip():
    r = PresignResponse(
        put_url="https://s3.example/sig",
        headers={"x-amz-checksum-sha256": "QkFTRTY0=="},
        upload_item_id=42,
        s3_uri="s3://qpress-uploads/dev/scans/1/images/aa.tif",
    )
    assert r.upload_item_id == 42


def test_complete_request_basic():
    c = CompleteRequest(width=1024, height=768)
    assert c.width == 1024


def test_finalize_response():
    f = FinalizeResponse(status="ready", missing=0)
    assert f.status == "ready"
```

### Step 1.3: Run — expect ImportError on the new schema names

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_upload_schemas.py -v
```

Expected: collection or import error — `PresignRequest`/`PresignResponse`/`CompleteRequest`/`FinalizeResponse` not yet exported from `flake_analysis.api.schemas.upload`.

### Step 1.4: Append schemas to upload.py

- [ ] **Append to `src/flake_analysis/api/schemas/upload.py` (W5-B1 file). Add the import at the top of the file (after the existing imports):**

```python
import re

from pydantic import field_validator

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
```

- [ ] **Append the new model classes at the end of the file:**

```python
# ---- scans (detail) ----

class ImageSummary(BaseModel):
    image_id: int
    grid_ix: int
    grid_iy: int
    s3_uri: str
    sha256: str


class ScanDetailResponse(BaseModel):
    scan_id: int
    name: str
    material: str
    image_count: int
    extra_metadata: dict[str, Any]
    uploaded_count: int
    grid_ix_range: tuple[int, int] | None
    grid_iy_range: tuple[int, int] | None
    images: list[ImageSummary]


# ---- presign / complete / finalize ----

class PresignRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    grid_ix: int = Field(ge=0)
    grid_iy: int = Field(ge=0)
    size_bytes: int = Field(gt=0, le=2_000_000_000)  # 2 GB hard cap

    @field_validator("sha256")
    @classmethod
    def _hex_lower(cls, v: str) -> str:
        if not _HEX64_RE.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v


class PresignResponse(BaseModel):
    put_url: str
    headers: dict[str, str]
    upload_item_id: int
    s3_uri: str


class CompleteRequest(BaseModel):
    width: int = Field(gt=0, le=200_000)
    height: int = Field(gt=0, le=200_000)


class CompleteResponse(BaseModel):
    image_id: int


class FinalizeResponse(BaseModel):
    status: str  # "ready" or "incomplete"
    missing: int  # 0 when status=="ready"
```

### Step 1.5: Run schemas test — expect PASS (existing 5 + new 5 = 10)

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_upload_schemas.py -v
```

Expected: 10 passed (5 from W5-B1 + 5 new in W5-B2).

### Step 1.6: Write the failing presign service test

- [ ] **Create `tests/api/test_s3_presign_service.py`:**

```python
"""Unit tests for s3_presign service (moto-backed)."""
from __future__ import annotations

import base64

import boto3
import pytest
from moto import mock_aws

from flake_analysis.api.services.s3_presign import (
    build_s3_key,
    hex_to_b64,
    presign_put,
)


def test_hex_to_b64_round_trip():
    sha = "a" * 64
    b64 = hex_to_b64(sha)
    raw = base64.b64decode(b64)
    assert raw == bytes.fromhex(sha)
    assert len(raw) == 32


def test_build_s3_key_uses_prefix_and_extension():
    key = build_s3_key(prefix="dev/", scan_id=42, sha256="b" * 64, filename="tile.TIF")
    assert key == "dev/scans/42/images/" + ("b" * 64) + ".tif"


def test_build_s3_key_handles_no_extension():
    key = build_s3_key(prefix="dev/", scan_id=1, sha256="c" * 64, filename="raw")
    assert key == "dev/scans/1/images/" + ("c" * 64) + ".bin"


@mock_aws
def test_presign_put_returns_url_with_checksum():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )
    sha = "d" * 64
    result = presign_put(
        bucket="qpress-uploads",
        key="dev/scans/1/images/" + sha + ".tif",
        sha256_hex=sha,
        expires_in=300,
    )
    assert result["put_url"].startswith("https://")
    assert "x-amz-checksum-sha256" in result["headers"]
    # base64 of 32 raw bytes is always 44 chars (incl. padding)
    assert len(result["headers"]["x-amz-checksum-sha256"]) == 44
```

### Step 1.7: Run — expect ImportError

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_s3_presign_service.py -v
```

Expected: ModuleNotFoundError on `flake_analysis.api.services.s3_presign`.

### Step 1.8: Implement presign service

- [ ] **Create `src/flake_analysis/api/services/s3_presign.py`:**

```python
"""S3 presigned-PUT helper for W5-B upload flow.

Generates a presigned PUT URL with the client-provided SHA256 baked into the
signature so S3 rejects mismatched bytes (BadDigest 400). Stateless — the
DB-side bookkeeping lives in upload_service.
"""
from __future__ import annotations

import base64
import os
from pathlib import PurePosixPath
from typing import TypedDict

import boto3
from botocore.config import Config


class PresignResult(TypedDict):
    put_url: str
    headers: dict[str, str]


def hex_to_b64(sha256_hex: str) -> str:
    """Convert 64-char hex SHA256 to the base64 form S3 expects."""
    raw = bytes.fromhex(sha256_hex)
    return base64.b64encode(raw).decode("ascii")


def _safe_extension(filename: str) -> str:
    """Lowercase ASCII extension without leading dot, defaulting to 'bin'."""
    suffix = PurePosixPath(filename).suffix.lstrip(".").lower()
    if not suffix or not suffix.isalnum():
        return "bin"
    return suffix


def build_s3_key(*, prefix: str, scan_id: int, sha256: str, filename: str) -> str:
    """Compose the content-addressed S3 key.

    Layout: `{prefix}scans/{scan_id}/images/{sha256}.{ext}` where prefix is
    expected to include its trailing slash (eg "dev/").
    """
    ext = _safe_extension(filename)
    return f"{prefix}scans/{scan_id}/images/{sha256}.{ext}"


def _client():
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-2")
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def presign_put(
    *,
    bucket: str,
    key: str,
    sha256_hex: str,
    expires_in: int = 300,
) -> PresignResult:
    """Issue a presigned PUT URL with x-amz-checksum-sha256 enforced."""
    sha_b64 = hex_to_b64(sha256_hex)
    url = _client().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ChecksumSHA256": sha_b64,
        },
        ExpiresIn=expires_in,
        HttpMethod="PUT",
    )
    return {
        "put_url": url,
        "headers": {"x-amz-checksum-sha256": sha_b64},
    }


def head_object(*, bucket: str, key: str) -> dict:
    """Return S3 head_object response. Caller catches ClientError on 404."""
    return _client().head_object(Bucket=bucket, Key=key)
```

### Step 1.9: Run presign test — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_s3_presign_service.py -v
```

Expected: 4 passed.

### Step 1.10: Append upload-session and upload-item helpers

- [ ] **Append to `src/flake_analysis/api/services/upload_service.py` (W5-B1 file). Add the imports near the top:**

```python
from flake_analysis.db.models.upload import (
    UploadItem,
    UploadItemStatus,
    UploadSession,
    UploadSessionStatus,
)
```

- [ ] **Append the new helpers at the end of the file:**

```python
async def get_or_create_upload_session(
    session: AsyncSession,
    *,
    scan: Scan,
    created_by_id: UUID,
) -> UploadSession:
    """Per scan, one active upload session. Reuse on subsequent presigns."""
    stmt = (
        select(UploadSession)
        .where(UploadSession.scan_id == scan.id)
        .where(UploadSession.status == UploadSessionStatus.ACTIVE)
        .limit(1)
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    upl = UploadSession(
        scan_id=scan.id,
        total_files=scan.image_count,
        created_by_id=created_by_id,
    )
    session.add(upl)
    await session.flush()
    await session.refresh(upl)
    return upl


async def create_upload_item(
    session: AsyncSession,
    *,
    upload_session: UploadSession,
    sha256: str,
    filename: str,
    size_bytes: int,
    grid_ix: int,
    grid_iy: int,
    s3_uri: str,
) -> UploadItem:
    """Insert a pending upload_item. Uniqueness on (session_id, sha256) is
    enforced by the existing DB constraint — caller catches IntegrityError
    and translates to 409.
    """
    item = UploadItem(
        session_id=upload_session.id,
        sha256=sha256,
        filename=filename,
        size_bytes=size_bytes,
        grid_ix=grid_ix,
        grid_iy=grid_iy,
        s3_uri=s3_uri,
        status=UploadItemStatus.PENDING,
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return item
```

### Step 1.11: Commit

- [ ] **Run:**

```bash
git add pyproject.toml uv.lock \
  src/flake_analysis/api/schemas/upload.py \
  src/flake_analysis/api/services/upload_service.py \
  src/flake_analysis/api/services/s3_presign.py \
  tests/api/test_upload_schemas.py \
  tests/api/test_s3_presign_service.py
git commit -m "feat(api): W5-B2.1 schemas extension + s3 presign service (boto3, moto-tested)"
```

---

## Task 2 — `POST /scans/{scan_id}/images/presign`

**Files:**
- Append: `src/flake_analysis/api/routes/scans.py`
- Create: `tests/api/test_scans_presign.py`

**Why:** The crown jewel of W5-B. Validates uniqueness pre-PUT, signs the SHA256 into the URL, creates the upload-session+upload-item rows. The `images` row is NOT inserted here — it lands at `complete` time.

### Step 2.1: Write the failing presign tests

- [ ] **Create `tests/api/test_scans_presign.py`:**

```python
"""W5-B2.2 — POST /scans/{scan_id}/images/presign tests."""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models.upload import (
    UploadItem,
    UploadItemStatus,
    UploadSession,
    UploadSessionStatus,
)

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


async def _create_scan(client, image_count=4):
    r = await client.post(
        "/api/v1/projects/local/scans",
        json={"name": "s1", "material": "graphene", "image_count": image_count},
    )
    assert r.status_code == 201, r.text
    return r.json()["scan_id"]


@pytest.mark.asyncio
@mock_aws
async def test_presign_creates_session_and_item(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id = await _create_scan(c)
            r = await c.post(
                f"/api/v1/scans/{scan_id}/images/presign",
                json={
                    "filename": "tile_0_0.tif",
                    "sha256": "a" * 64,
                    "grid_ix": 0,
                    "grid_iy": 0,
                    "size_bytes": 10485760,
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["put_url"].startswith("https://")
            assert "x-amz-checksum-sha256" in body["headers"]
            assert isinstance(body["upload_item_id"], int)
            assert body["s3_uri"].startswith("s3://qpress-uploads/dev/scans/")
            # DB side: 1 active session, 1 pending item
            sess = (await pg_session.execute(
                select(UploadSession).where(UploadSession.scan_id == scan_id)
            )).scalar_one()
            assert sess.status == UploadSessionStatus.ACTIVE
            assert sess.total_files == 4
            item = (await pg_session.execute(
                select(UploadItem).where(UploadItem.id == body["upload_item_id"])
            )).scalar_one()
            assert item.status == UploadItemStatus.PENDING
            assert item.sha256 == "a" * 64
            assert item.grid_ix == 0
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_presign_rejects_duplicate_sha256(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id = await _create_scan(c)
            body = {
                "filename": "a.tif", "sha256": "b" * 64,
                "grid_ix": 0, "grid_iy": 0, "size_bytes": 100,
            }
            ok = await c.post(f"/api/v1/scans/{scan_id}/images/presign", json=body)
            assert ok.status_code == 200
            dup = await c.post(
                f"/api/v1/scans/{scan_id}/images/presign",
                json={**body, "grid_ix": 1},  # different grid, same sha
            )
            assert dup.status_code == 409
            assert "sha256" in dup.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_presign_rejects_duplicate_grid(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id = await _create_scan(c)
            ok = await c.post(
                f"/api/v1/scans/{scan_id}/images/presign",
                json={"filename": "a.tif", "sha256": "c" * 64,
                      "grid_ix": 2, "grid_iy": 3, "size_bytes": 100},
            )
            assert ok.status_code == 200
            dup = await c.post(
                f"/api/v1/scans/{scan_id}/images/presign",
                json={"filename": "b.tif", "sha256": "d" * 64,
                      "grid_ix": 2, "grid_iy": 3, "size_bytes": 100},
            )
            assert dup.status_code == 409
            assert "grid" in dup.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_presign_404_when_scan_missing(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/scans/9999999/images/presign",
                json={"filename": "a.tif", "sha256": "e" * 64,
                      "grid_ix": 0, "grid_iy": 0, "size_bytes": 1},
            )
            assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)
```

### Step 2.2: Run — expect 404 / route missing

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_scans_presign.py -v
```

Expected: 4 failed.

### Step 2.3: Implement presign endpoint

- [ ] **Append to `src/flake_analysis/api/routes/scans.py` (W5-B1 file). Add imports at the top of the file (after the existing imports):**

```python
import os

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from flake_analysis.api.schemas.upload import (
    PresignRequest,
    PresignResponse,
)
from flake_analysis.api.services import s3_presign
from flake_analysis.db.models import Scan
from flake_analysis.db.models.upload import Image, UploadItem
```

- [ ] **Append the route handler at the end of the file:**

```python
@router.post(
    "/scans/{scan_id}/images/presign",
    response_model=PresignResponse,
)
async def presign_image_put(
    scan_id: int,
    req: PresignRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PresignResponse:
    """Issue a presigned PUT URL with SHA256 baked into the signature.

    Pre-checks (scan_id, sha256) and (scan_id, grid_ix, grid_iy) for collisions
    against `images` (already-uploaded) AND active upload_items (in-flight) so
    duplicate work fails fast with 409 before any URL is signed.
    """
    bucket = os.environ.get("SAA_S3_BUCKET")
    prefix = os.environ.get("SAA_S3_PREFIX", "")
    if not bucket:
        raise HTTPException(status_code=500, detail="SAA_S3_BUCKET not configured")

    scan = (await session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")

    # 1) sha256 collision with finalized images
    img_dup = (await session.execute(
        select(Image.id)
        .where(Image.scan_id == scan_id)
        .where(Image.sha256 == req.sha256)
    )).scalar_one_or_none()
    if img_dup is not None:
        raise HTTPException(
            status_code=409,
            detail=f"sha256 already uploaded as image {img_dup}",
        )
    # 2) grid collision with finalized images
    grid_dup = (await session.execute(
        select(Image.id)
        .where(Image.scan_id == scan_id)
        .where(Image.grid_ix == req.grid_ix)
        .where(Image.grid_iy == req.grid_iy)
    )).scalar_one_or_none()
    if grid_dup is not None:
        raise HTTPException(
            status_code=409,
            detail=f"grid ({req.grid_ix},{req.grid_iy}) already uploaded as image {grid_dup}",
        )

    upl = await upload_service.get_or_create_upload_session(
        session, scan=scan, created_by_id=user.id,
    )

    # 3) sha256 collision with in-flight upload_item (same session)
    inflight_sha = (await session.execute(
        select(UploadItem.id)
        .where(UploadItem.session_id == upl.id)
        .where(UploadItem.sha256 == req.sha256)
    )).scalar_one_or_none()
    if inflight_sha is not None:
        raise HTTPException(
            status_code=409,
            detail=f"sha256 already in-flight as upload_item {inflight_sha}",
        )
    # 4) grid collision with in-flight upload_item (same session)
    inflight_grid = (await session.execute(
        select(UploadItem.id)
        .where(UploadItem.session_id == upl.id)
        .where(UploadItem.grid_ix == req.grid_ix)
        .where(UploadItem.grid_iy == req.grid_iy)
    )).scalar_one_or_none()
    if inflight_grid is not None:
        raise HTTPException(
            status_code=409,
            detail=f"grid ({req.grid_ix},{req.grid_iy}) already in-flight as upload_item {inflight_grid}",
        )

    key = s3_presign.build_s3_key(
        prefix=prefix, scan_id=scan_id, sha256=req.sha256, filename=req.filename,
    )
    s3_uri = f"s3://{bucket}/{key}"

    try:
        item = await upload_service.create_upload_item(
            session,
            upload_session=upl,
            sha256=req.sha256,
            filename=req.filename,
            size_bytes=req.size_bytes,
            grid_ix=req.grid_ix,
            grid_iy=req.grid_iy,
            s3_uri=s3_uri,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"upload_item insert conflict: {exc.orig}") from exc

    presigned = s3_presign.presign_put(
        bucket=bucket, key=key, sha256_hex=req.sha256, expires_in=300,
    )
    await session.commit()

    return PresignResponse(
        put_url=presigned["put_url"],
        headers=presigned["headers"],
        upload_item_id=item.id,
        s3_uri=s3_uri,
    )
```

### Step 2.4: Run presign tests — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_scans_presign.py -v
```

Expected: 4 passed.

### Step 2.5: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scans_presign.py
git commit -m "feat(api): W5-B2.2 POST /scans/{id}/images/presign with sha256 enforcement"
```

---

## Task 3 — `POST /scans/{scan_id}/images/{upload_item_id}/complete`

**Files:**
- Append: `src/flake_analysis/api/routes/scans.py`
- Create: `tests/api/test_scans_complete.py`

**Why:** After the browser PUTs to S3 successfully, this endpoint promotes the in-flight upload_item to a canonical `images` row. We `head_object` to confirm S3 has the bytes (defense in depth: if the client lies about a successful PUT, S3 says no-such-key and we 409).

### Step 3.1: Write the failing complete tests

- [ ] **Create `tests/api/test_scans_complete.py`:**

```python
"""W5-B2.3 — POST /scans/{sid}/images/{uid}/complete tests."""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models.upload import (
    Image,
    UploadItem,
    UploadItemStatus,
)

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


async def _scan_and_presign(client, sha="a" * 64, ix=0, iy=0):
    sr = await client.post(
        "/api/v1/projects/local/scans",
        json={"name": "s1", "material": "graphene", "image_count": 2},
    )
    scan_id = sr.json()["scan_id"]
    pr = await client.post(
        f"/api/v1/scans/{scan_id}/images/presign",
        json={"filename": "t.tif", "sha256": sha,
              "grid_ix": ix, "grid_iy": iy, "size_bytes": 100},
    )
    return scan_id, pr.json()


def _put_object(key: str, body: bytes = b"x"):
    boto3.client("s3", region_name="us-east-2").put_object(
        Bucket="qpress-uploads", Key=key, Body=body,
    )


@pytest.mark.asyncio
@mock_aws
async def test_complete_inserts_image_row(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id, presign = await _scan_and_presign(c)
            # Simulate a successful S3 PUT
            key = presign["s3_uri"].split("/", 3)[-1]
            _put_object(key)

            r = await c.post(
                f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                json={"width": 1024, "height": 768},
            )
            assert r.status_code == 200, r.text
            image_id = r.json()["image_id"]

            img = (await pg_session.execute(
                select(Image).where(Image.id == image_id)
            )).scalar_one()
            assert img.scan_id == scan_id
            assert img.width == 1024 and img.height == 768
            assert img.sha256 == "a" * 64

            item = (await pg_session.execute(
                select(UploadItem).where(UploadItem.id == presign["upload_item_id"])
            )).scalar_one()
            assert item.status == UploadItemStatus.UPLOADED
            assert item.image_id == image_id
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_complete_409_when_s3_object_missing(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id, presign = await _scan_and_presign(c)
            # Do NOT put the object — head_object should 404 → API 409
            r = await c.post(
                f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                json={"width": 10, "height": 10},
            )
            assert r.status_code == 409
            assert "s3" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_complete_404_when_upload_item_missing(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            sr = await c.post(
                "/api/v1/projects/local/scans",
                json={"name": "s1", "material": "graphene", "image_count": 1},
            )
            scan_id = sr.json()["scan_id"]
            r = await c.post(
                f"/api/v1/scans/{scan_id}/images/9999999/complete",
                json={"width": 10, "height": 10},
            )
            assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_complete_is_idempotent_on_already_uploaded(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id, presign = await _scan_and_presign(c)
            key = presign["s3_uri"].split("/", 3)[-1]
            _put_object(key)
            r1 = await c.post(
                f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                json={"width": 10, "height": 10},
            )
            assert r1.status_code == 200
            r2 = await c.post(
                f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                json={"width": 10, "height": 10},
            )
            assert r2.status_code == 200
            assert r2.json()["image_id"] == r1.json()["image_id"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)
```

### Step 3.2: Run — expect 404 / route missing

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_scans_complete.py -v
```

Expected: 4 failed.

### Step 3.3: Implement complete endpoint

- [ ] **Append to `src/flake_analysis/api/routes/scans.py`. Add imports at the top of the file (alongside the existing imports):**

```python
from botocore.exceptions import ClientError

from flake_analysis.api.schemas.upload import (
    CompleteRequest,
    CompleteResponse,
)
from flake_analysis.db.models.upload import UploadItemStatus
```

- [ ] **Append the route handler at the end of the file:**

```python
@router.post(
    "/scans/{scan_id}/images/{upload_item_id}/complete",
    response_model=CompleteResponse,
)
async def complete_image(
    scan_id: int,
    upload_item_id: int,
    req: CompleteRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CompleteResponse:
    """Promote a pending upload_item to a canonical images row.

    Idempotent: if the upload_item is already UPLOADED with image_id set,
    return that image_id without re-running head_object or re-inserting.
    """
    bucket = os.environ.get("SAA_S3_BUCKET")
    if not bucket:
        raise HTTPException(status_code=500, detail="SAA_S3_BUCKET not configured")

    item = (await session.execute(
        select(UploadItem)
        .join(UploadItem.session)
        .where(UploadItem.id == upload_item_id)
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"upload_item {upload_item_id} not found")
    if item.session.scan_id != scan_id:
        raise HTTPException(
            status_code=404,
            detail=f"upload_item {upload_item_id} does not belong to scan {scan_id}",
        )

    # Idempotency short-circuit
    if item.status == UploadItemStatus.UPLOADED and item.image_id is not None:
        return CompleteResponse(image_id=item.image_id)

    # Verify the S3 object exists
    if item.s3_uri is None or not item.s3_uri.startswith(f"s3://{bucket}/"):
        raise HTTPException(status_code=409, detail="upload_item has invalid s3_uri")
    key = item.s3_uri[len(f"s3://{bucket}/"):]
    try:
        s3_presign.head_object(bucket=bucket, key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise HTTPException(
                status_code=409,
                detail=f"S3 object {key} not found — upload did not complete",
            ) from exc
        raise HTTPException(status_code=500, detail=f"S3 head_object failed: {code}") from exc

    # Insert canonical Image row
    image = Image(
        scan_id=scan_id,
        sha256=item.sha256,
        s3_uri=item.s3_uri,
        width=req.width,
        height=req.height,
        filename=item.filename,
        grid_ix=item.grid_ix if item.grid_ix is not None else 0,
        grid_iy=item.grid_iy if item.grid_iy is not None else 0,
    )
    session.add(image)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"image insert conflict: {exc.orig}") from exc

    item.status = UploadItemStatus.UPLOADED
    item.image_id = image.id
    await session.commit()
    return CompleteResponse(image_id=image.id)
```

### Step 3.4: Run complete tests — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_scans_complete.py -v
```

Expected: 4 passed.

### Step 3.5: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scans_complete.py
git commit -m "feat(api): W5-B2.3 POST /scans/{id}/images/{uid}/complete with S3 head verification"
```

---

## Task 4 — Finalize + GET scan, then full acceptance gate

**Files:**
- Append: `src/flake_analysis/api/routes/scans.py`
- Create: `tests/api/test_scans_finalize.py`

**Why:** Last two endpoints + the cross-cutting acceptance run. `finalize` checks `count(images) == scans.image_count` and emits a `usage_events` row of kind `scan_uploaded` for admin visibility. `GET /scans/{id}` is the read-side counterpart used by the frontend modal to refresh on a partially-completed upload.

### Step 4.1: Write failing finalize + get tests

- [ ] **Create `tests/api/test_scans_finalize.py`:**

```python
"""W5-B2.4 — POST /scans/{id}/finalize and GET /scans/{id} tests."""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models import UsageEvent

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


async def _full_upload(client, scan_image_count: int, n: int):
    """Helper: create a scan with image_count, complete `n` images."""
    sr = await client.post(
        "/api/v1/projects/local/scans",
        json={"name": "s1", "material": "graphene", "image_count": scan_image_count},
    )
    scan_id = sr.json()["scan_id"]
    for i in range(n):
        sha = (f"{i:02x}" * 32)
        pr = await client.post(
            f"/api/v1/scans/{scan_id}/images/presign",
            json={"filename": f"t{i}.tif", "sha256": sha,
                  "grid_ix": i, "grid_iy": 0, "size_bytes": 100},
        )
        body = pr.json()
        key = body["s3_uri"].split("/", 3)[-1]
        boto3.client("s3", region_name="us-east-2").put_object(
            Bucket="qpress-uploads", Key=key, Body=b"x",
        )
        await client.post(
            f"/api/v1/scans/{scan_id}/images/{body['upload_item_id']}/complete",
            json={"width": 10, "height": 10},
        )
    return scan_id


@pytest.mark.asyncio
@mock_aws
async def test_finalize_ready_when_count_matches(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id = await _full_upload(c, scan_image_count=3, n=3)
            r = await c.post(f"/api/v1/scans/{scan_id}/finalize")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "ready"
            assert body["missing"] == 0

            usage = (await pg_session.execute(
                select(UsageEvent).where(UsageEvent.kind == "scan_uploaded")
            )).scalars().all()
            assert len(usage) == 1
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_finalize_409_when_incomplete(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id = await _full_upload(c, scan_image_count=5, n=2)
            r = await c.post(f"/api/v1/scans/{scan_id}/finalize")
            assert r.status_code == 409
            body = r.json()
            assert body["detail"]["status"] == "incomplete"
            assert body["detail"]["missing"] == 3
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_get_scan_detail(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            scan_id = await _full_upload(c, scan_image_count=4, n=2)
            r = await c.get(f"/api/v1/scans/{scan_id}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["scan_id"] == scan_id
            assert body["uploaded_count"] == 2
            assert body["image_count"] == 4
            assert body["grid_ix_range"] == [0, 1]
            assert body["grid_iy_range"] == [0, 0]
            assert len(body["images"]) == 2
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
@mock_aws
async def test_get_scan_404(pg_session):
    _create_bucket()
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/scans/9999999")
            assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db_session, None)
```

### Step 4.2: Run — expect failures

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_scans_finalize.py -v
```

Expected: 4 failed.

### Step 4.3: Implement finalize + get

- [ ] **Append to `src/flake_analysis/api/routes/scans.py`. Add imports at the top of the file:**

```python
from sqlalchemy import func

from flake_analysis.api.schemas.upload import (
    FinalizeResponse,
    ImageSummary,
    ScanDetailResponse,
)
from flake_analysis.api.services.usage import emit as emit_usage
```

- [ ] **Append the route handlers at the end of the file:**

```python
@router.post(
    "/scans/{scan_id}/finalize",
    response_model=FinalizeResponse,
)
async def finalize_scan(
    scan_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FinalizeResponse:
    scan = (await session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")

    uploaded = (await session.execute(
        select(func.count(Image.id)).where(Image.scan_id == scan_id)
    )).scalar_one()
    missing = max(scan.image_count - int(uploaded), 0)
    if missing > 0:
        raise HTTPException(
            status_code=409,
            detail={"status": "incomplete", "missing": missing,
                    "uploaded": int(uploaded), "expected": scan.image_count},
        )

    await emit_usage(session, user, "scan_uploaded", {"scan_id": scan_id})
    await session.commit()
    return FinalizeResponse(status="ready", missing=0)


@router.get(
    "/scans/{scan_id}",
    response_model=ScanDetailResponse,
)
async def get_scan(
    scan_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScanDetailResponse:
    scan = (await session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")

    images = (await session.execute(
        select(Image).where(Image.scan_id == scan_id).order_by(Image.id)
    )).scalars().all()
    if images:
        ix_vals = [im.grid_ix for im in images]
        iy_vals = [im.grid_iy for im in images]
        ix_range: tuple[int, int] | None = (min(ix_vals), max(ix_vals))
        iy_range: tuple[int, int] | None = (min(iy_vals), max(iy_vals))
    else:
        ix_range = None
        iy_range = None

    return ScanDetailResponse(
        scan_id=scan.id,
        name=scan.name,
        material=scan.material,
        image_count=scan.image_count,
        extra_metadata=scan.extra_metadata,
        uploaded_count=len(images),
        grid_ix_range=ix_range,
        grid_iy_range=iy_range,
        images=[
            ImageSummary(
                image_id=im.id, grid_ix=im.grid_ix, grid_iy=im.grid_iy,
                s3_uri=im.s3_uri, sha256=im.sha256,
            )
            for im in images
        ],
    )
```

### Step 4.4: Run finalize tests — expect PASS

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_scans_finalize.py -v
```

Expected: 4 passed.

### Step 4.5: Full W5-B suite — no regression

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api/test_upload_schemas.py tests/api/test_s3_presign_service.py tests/api/test_materials_routes.py tests/api/test_scans_create.py tests/api/test_scans_presign.py tests/api/test_scans_complete.py tests/api/test_scans_finalize.py -v
```

Expected: 32 passed (10 schema + 4 presign-svc + 3 materials + 3 scan-create + 4 presign + 4 complete + 4 finalize).

### Step 4.6: Broader tests/api PG suite — no regression

- [ ] **Run:**

```
SAA_TEST_DATABASE_URL='postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test' \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
SAA_S3_BUCKET=qpress-uploads SAA_S3_PREFIX=dev/ \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-2 \
uv run pytest tests/api -m pg --ignore=tests/scripts -q
```

Expected: pre-W5-B baseline (W5-A's 31) + W5-B1 (11) + W5-B2 (21) = ~63 passed (or whatever the `-m pg` count is post-W5-A; the delta from end-of-W5-B1 MUST be exactly +21). No prior tests fail.

### Step 4.7: Commit

- [ ] **Run:**

```bash
git add src/flake_analysis/api/routes/scans.py tests/api/test_scans_finalize.py
git commit -m "feat(api): W5-B2.4 finalize + GET scan + acceptance gate green"
```

### Step 4.8: Update project-status

- [ ] **In `docs/project-status.md` §3.1, append:**

> 2026-05-22 — W5-B2 백엔드 API (presign + complete + finalize + GET scan) 완료. 4개 엔드포인트 추가, `tests/api -m pg` +21 통과. W5-B 전체 종료. 다음: W5-C (프론트엔드 업로드 모달) + W5-infra (S3 버킷 + IAM).

```bash
git add docs/project-status.md
git commit -m "docs(status): mark W5-B2 complete — full upload API green on saa_test"
```

---

## Self-Review

**Spec coverage (W5-B2 scope):**
- D1 bucket + prefix from env (`SAA_S3_BUCKET`, `SAA_S3_PREFIX`) → Task 1.8 reads, Task 2 uses. ✓
- D2 SHA256 client-side, signed into presign → Task 1.8 `presign_put` passes `ChecksumSHA256`, response returns the b64 in `headers`. ✓
- D3 per-scan upload session → Task 1.10 `get_or_create_upload_session` filters by `scan_id` + `ACTIVE`. No cross-page resumability built; client refresh = new scan. ✓
- D4 user-input metadata: `material` controlled vocab with auto-add (W5-B1), per-file `grid_ix/grid_iy` 0-based, `extra_metadata` JSONB → schemas in Task 1.4 + routes in Tasks 2/3/4. ✓
- D5 auth: every route depends on `get_current_user`, `created_by_id` populated from `user.id`. ✓
- D6 routing: `/api/v1/scans/{sid}/...` for everything in this plan; `POST /projects/{pid}/scans` shipped in W5-B1. ✓
- All 4 endpoints in this plan: `POST /scans/{sid}/images/presign`, `POST /scans/{sid}/images/{uid}/complete`, `POST /scans/{sid}/finalize`, `GET /scans/{sid}`. ✓

**Placeholder scan:** none. No `# TODO`, no `// TODO`, no `[fill in]`, no `pass`-stubs. All appended sections are full implementations.

**Type consistency:**
- `images.sha256` is `CHAR(64)` and `Image.sha256: Mapped[str]` — schema enforces exactly 64 lowercase hex via `_HEX64_RE`. ✓
- `s3_uri` always full `s3://bucket/key` — built in Task 2.3, returned by presign + stored on `images`. ✓
- `extra_metadata: Mapped[dict]` (W5-A) round-trips through `dict[str, Any]` pydantic. ✓
- `grid_ix/grid_iy: Mapped[int]` (W5-A NOT NULL) — schema enforces `ge=0`. The `complete` route falls back to `0` only if upload_item.grid_ix is None, which can't happen because the presign route always sets it. The fallback is defense-in-depth.

**Edge cases:**
- Re-presign with same sha256 → 409 (Task 2 covers in-flight + finalized).
- Re-presign with same grid → 409 (same).
- PUT-but-don't-complete then page refresh: a stale `pending` upload_item lingers. v1 acceptable per D3 (refresh = new scan). Cleanup is a future cron concern.
- `complete` called twice → idempotent return of same image_id (Task 3.1 test).
- `finalize` called when one image still pending → 409 with `missing` count (Task 4.1 test).
- moto `mock_aws` decorator: applied per-test, not per-fixture, because moto state must reset between tests.

**Boundary risks:**
- moto + asyncpg interaction: moto mocks boto3 (sync), and our presign service uses sync boto3. No async S3. ✓
- W5-B1 file shape assumption: Tasks 1/2/3/4 all append to W5-B1's `scans.py`, `upload.py`, `upload_service.py`. The append targets are the end-of-file in each case; if W5-B1's files have evolved on main since this plan was written, the agent must reconcile (the imports listed at the top of each "implement" step are additive — duplicate imports should be deduplicated when found).

---

## Open follow-up (out of W5-B2 scope)

### Resolved (carried from W5-B1)

- **`projects` table missing in saa_test / whether to add `scans.project_id` FK now or defer.** Locked 2026-05-22: v1 leaves `project_id` as path-only routing. v2 will introduce a `projects` table and add `scans.project_id` FK in a single migration that also rewrites manifest-based pipelines. Documented in W5-B1.

### Carried forward

1. **Resumability deferred per D3.** Per-scan upload session is per page-load. A future improvement: `GET /scans/{id}/upload-session` returning the active session id + per-tile status, so a page refresh can pick up where the user left off. Not in v1 scope.

2. **`upload_sessions.completed_files` / `failed_files` counters** are not maintained by this plan — the route layer treats them as informational only, since `finalize` re-counts `images` from scratch. If we ever want a long-running upload progress meter, a follow-up will trigger DB-side updates from `complete`.

3. **`upload_sessions` being marked COMPLETED at finalize.** Currently `finalize` only checks counts and emits a usage event; it does NOT flip `upload_session.status` to `COMPLETED`. Decide in a follow-up whether to do so (cosmetic; nothing reads it yet outside admin queries).

4. **`pg_session` savepoint semantics under HTTPException.** When a route raises HTTPException(409) after `session.flush()`, the test's outer transaction-scoped `pg_session` may be in a rolled-back state. Tests use the conftest `pg_session` which is a savepoint-per-test pattern; verify by running Task 2.4 — if 409 tests bleed state, switch to explicit `session.rollback()` calls in the route (already done in IntegrityError branches). If a regression appears in CI under load, file a follow-up to formalize the savepoint contract in `conftest.py`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-W5-B2-presign-complete-finalize.md`.

**Recommended execution mode:** Subagent-Driven. 4 tasks total — 1 leaf-level (schemas extension + presign service + service helpers), 3 scan-flow steps (presign → complete → finalize+get). Each task ~10–15 min implementer + spec review + code review.

**Dispatch order:** Step 0.1 verify dependency → 1 → 2 → 3 → 4 (strict; each task depends on prior — Task 2 imports Task 1's schemas + service, Task 3 builds on Task 2's presign helper in tests, Task 4 chains the full upload in its test helper).

**Pre-flight check before Task 1:**
1. Confirm Step 0.1 passes (W5-B1 merged on main, source files exist).
2. Confirm alembic head on saa_test is `0003_w5a_materials_uploads` (W5-A complete). If not, halt and dispatch db-specialist to apply.
