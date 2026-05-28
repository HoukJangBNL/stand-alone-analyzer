"""GET /gpu/status — lazy probe + 30s memory cache.

Verifies state derivation order (running > launching > ready >
unavailable_capacity > unknown), cache TTL, and auth gating. Stubs
boto3 EC2 calls via botocore.stub.Stubber so tests don't hit AWS.

State derivation order (first match wins):
  1. running                — describe_instances has any pending|running
                               instance with Project=qpress-sam, Role=worker
                               in 'running' state.
  2. launching              — same query, but only 'pending' instances.
  3. ready                  — no live instance + describe_spot_price_history
                               returns prices in last 1h across AZs.
  4. unavailable_capacity   — no live instance + spot price history empty
                               OR all timestamps stale (>1h old).
  5. unknown                — botocore ClientError or other AWS failure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from botocore.stub import Stubber
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import app
from flake_analysis.api.services import gpu_status as gpu_status_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _instance(state: str, az: str = "us-east-2a") -> dict[str, Any]:
    return {
        "InstanceId": f"i-fake-{state}",
        "InstanceType": "g6e.48xlarge",
        "State": {"Name": state, "Code": 16 if state == "running" else 0},
        "Placement": {"AvailabilityZone": az},
        "Tags": [
            {"Key": "Project", "Value": "qpress-sam"},
            {"Key": "Role", "Value": "worker"},
        ],
    }


def _describe_instances_response(*instances: dict[str, Any]) -> dict[str, Any]:
    if not instances:
        return {"Reservations": []}
    return {"Reservations": [{"Instances": list(instances)}]}


def _spot_price_history_response(prices_by_az: dict[str, float],
                                 timestamp: datetime | None = None) -> dict[str, Any]:
    ts = timestamp or _utcnow()
    return {
        "SpotPriceHistory": [
            {
                "AvailabilityZone": az,
                "InstanceType": "g6e.48xlarge",
                "ProductDescription": "Linux/UNIX",
                "SpotPrice": str(price),
                "Timestamp": ts,
            }
            for az, price in prices_by_az.items()
        ]
    }


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the module-level cache between tests."""
    gpu_status_service.reset_cache_for_tests()
    yield
    gpu_status_service.reset_cache_for_tests()


@pytest.fixture
def stubbed_ec2(monkeypatch):
    """Inject a Stubber-wrapped EC2 client into the service.

    Returns the Stubber so tests can queue responses. Service factory is
    monkeypatched to return the stubbed client.
    """
    import boto3

    client = boto3.client("ec2", region_name="us-east-2")
    stubber = Stubber(client)
    stubber.activate()

    monkeypatch.setattr(gpu_status_service, "_ec2_client_factory", lambda: client)
    yield stubber
    stubber.deactivate()


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_running_when_instance_pending_or_running(stubbed_ec2):
    """describe_instances returns a running instance → state='running'."""
    stubbed_ec2.add_response(
        "describe_instances",
        _describe_instances_response(_instance("running", az="us-east-2b")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/gpu/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "running"
    assert "us-east-2b" in body["detail"]
    stubbed_ec2.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_state_launching_when_only_pending(stubbed_ec2):
    """All live instances in 'pending' (none running) → state='launching'."""
    stubbed_ec2.add_response(
        "describe_instances",
        _describe_instances_response(_instance("pending", az="us-east-2c")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/gpu/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "launching"
    stubbed_ec2.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_state_ready_when_no_instance_recent_prices(stubbed_ec2):
    """No live instance + fresh spot prices in 3 AZs → state='ready'."""
    stubbed_ec2.add_response(
        "describe_instances",
        _describe_instances_response(),  # no instances
    )
    stubbed_ec2.add_response(
        "describe_spot_price_history",
        _spot_price_history_response(
            {"us-east-2a": 4.61, "us-east-2b": 4.55, "us-east-2c": 4.70},
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/gpu/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "ready"
    assert body["spot_prices_usd_per_hr"] == {
        "us-east-2a": 4.61,
        "us-east-2b": 4.55,
        "us-east-2c": 4.70,
    }
    stubbed_ec2.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_state_unavailable_capacity_when_prices_stale(stubbed_ec2):
    """No live instance + spot price history empty → state='unavailable_capacity'."""
    stubbed_ec2.add_response(
        "describe_instances",
        _describe_instances_response(),
    )
    stubbed_ec2.add_response(
        "describe_spot_price_history",
        {"SpotPriceHistory": []},  # empty
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/gpu/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "unavailable_capacity"
    assert "spot prices" in body["detail"].lower() or "capacity" in body["detail"].lower()
    stubbed_ec2.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_state_unknown_on_botocore_error(stubbed_ec2):
    """describe_instances raises ClientError → state='unknown'."""
    stubbed_ec2.add_client_error(
        "describe_instances",
        service_error_code="UnauthorizedOperation",
        service_message="not authorized",
        http_status_code=403,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/gpu/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "unknown"
    assert body["spot_prices_usd_per_hr"] is None
    # detail should mention the error type / code
    assert "Unauthorized" in body["detail"] or "ClientError" in body["detail"]


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_within_30s(stubbed_ec2):
    """Two calls in <30s → AWS hit only once."""
    # Only ONE describe_instances response queued — second call must use cache.
    stubbed_ec2.add_response(
        "describe_instances",
        _describe_instances_response(_instance("running")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.get("/api/v1/gpu/status")
        r2 = await c.get("/api/v1/gpu/status")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # checked_at identical → served from cache
    assert r1.json()["checked_at"] == r2.json()["checked_at"]
    stubbed_ec2.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_cache_miss_after_30s(stubbed_ec2, monkeypatch):
    """Advance clock >30s between calls → AWS hit twice."""
    # Queue TWO describe_instances responses — second call refreshes cache.
    stubbed_ec2.add_response(
        "describe_instances",
        _describe_instances_response(_instance("running", az="us-east-2a")),
    )
    stubbed_ec2.add_response(
        "describe_instances",
        _describe_instances_response(_instance("running", az="us-east-2c")),
    )

    fake_now = [_utcnow()]

    def _now():
        return fake_now[0]

    monkeypatch.setattr(gpu_status_service, "_now_utc", _now)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.get("/api/v1/gpu/status")
        # Advance 31s
        fake_now[0] = fake_now[0] + timedelta(seconds=31)
        r2 = await c.get("/api/v1/gpu/status")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # checked_at differs → cache was bypassed
    assert r1.json()["checked_at"] != r2.json()["checked_at"]
    # Detail mentions the refreshed AZ
    assert "us-east-2c" in r2.json()["detail"]
    stubbed_ec2.assert_no_pending_responses()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requires_auth(monkeypatch, stubbed_ec2):
    """No bearer token → 401."""
    monkeypatch.delenv("SAA_AUTH_DEV_BYPASS", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/gpu/status")
    assert r.status_code == 401
