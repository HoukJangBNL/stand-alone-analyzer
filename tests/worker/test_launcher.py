"""P4.4 — API-side spot launcher unit tests.

The launcher is the API-side seam that boots a GPU worker EC2 instance
on demand when there is no live worker draining the procrastinate
``gpu`` queue. It is invoked just before ``defer_async`` from the SAM
route(s).

Behaviors under test
--------------------
1. Worker already running → does NOT call run-instances.
2. No worker → calls run-instances exactly once with the right launch
   template name (``qpress-sam-gpu-worker``) at the default version.
3. Race protection: two concurrent ``ensure_worker_running()`` calls
   arriving within the same boot window result in **one** RunInstances
   call. The second caller acquires the same advisory lock, sees a
   worker already in pending state, and is a no-op.
4. ``InsufficientInstanceCapacity`` from boto3 → surfaces as a
   :class:`flake_analysis.worker.launcher.GpuCapacityUnavailable` error
   so the API can wire it through the existing pipeline_error envelope.

The launcher uses a synchronous boto3 client wrapped in an executor so
the API's event loop is not blocked. Tests inject a fake EC2 client
factory — no real boto3 client, no AWS calls.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Fake EC2 client
# ---------------------------------------------------------------------------


class FakeEc2Client:
    """In-memory stand-in for a boto3 EC2 client.

    Records describe-instances/run-instances calls and returns scripted
    responses. Mirrors only the slice of the boto3 surface the launcher
    actually touches — not a full mock of the EC2 API.
    """

    def __init__(self) -> None:
        self.describe_calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []
        # Configurable response state
        self._existing_instances: list[dict[str, Any]] = []
        self._run_error: ClientError | None = None
        self._run_response: dict[str, Any] = {
            "Instances": [{"InstanceId": "i-fake12345"}]
        }

    # --- describe_instances ------------------------------------------------
    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_calls.append(kwargs)
        return {
            "Reservations": [
                {"Instances": self._existing_instances}
            ]
            if self._existing_instances
            else []
        }

    def set_existing_workers(self, *states: str) -> None:
        """Each arg is a state name like 'running', 'pending', 'terminated'."""
        self._existing_instances = [
            {
                "InstanceId": f"i-existing{i}",
                "State": {"Name": state},
                "Tags": [
                    {"Key": "Project", "Value": "qpress-sam"},
                    {"Key": "Role", "Value": "worker"},
                ],
            }
            for i, state in enumerate(states)
        ]

    # --- run_instances -----------------------------------------------------
    def run_instances(self, **kwargs: Any) -> dict[str, Any]:
        if self._run_error is not None:
            raise self._run_error
        self.run_calls.append(kwargs)
        return self._run_response

    def set_run_error(self, code: str, message: str) -> None:
        self._run_error = ClientError(
            {"Error": {"Code": code, "Message": message}},
            "RunInstances",
        )


# ---------------------------------------------------------------------------
# Fake advisory-lock helper
# ---------------------------------------------------------------------------


class FakeAdvisoryLock:
    """Stand-in for the PG advisory lock used to serialise boot windows.

    Tests don't have a real PG session here. The launcher resolves the
    lock through an injectable async callable; this fake records calls
    and lets the test simulate "lock already held by another caller".
    """

    def __init__(self) -> None:
        self.acquire_calls: int = 0
        self.release_calls: int = 0
        self._holder_count: int = 0

    async def acquire(self) -> bool:
        self.acquire_calls += 1
        if self._holder_count > 0:
            return False  # someone else holds the lock
        self._holder_count = 1
        return True

    async def release(self) -> None:
        self.release_calls += 1
        self._holder_count = max(0, self._holder_count - 1)

    def force_held(self) -> None:
        """Pretend a different connection already holds the lock."""
        self._holder_count = 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_worker_does_not_boot_new_instance():
    """If a tagged worker is already RUNNING, ensure_worker_running is a no-op."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_existing_workers("running")
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "noop"
    assert result.reason == "worker_already_running"
    # No boot attempted.
    assert ec2.run_calls == []
    # We still acquire/release the lock so the result is serialised.
    assert lock.acquire_calls == 1
    assert lock.release_calls == 1


@pytest.mark.asyncio
async def test_pending_worker_does_not_boot_new_instance():
    """A worker in 'pending' is mid-boot; don't launch another."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_existing_workers("pending")
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "noop"
    assert result.reason == "worker_already_running"
    assert ec2.run_calls == []


@pytest.mark.asyncio
async def test_no_worker_boots_one_via_launch_template():
    """Empty fleet → launcher calls run-instances with the launch template name."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    # No existing workers.
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "launched"
    assert result.instance_id == "i-fake12345"
    assert len(ec2.run_calls) == 1
    call = ec2.run_calls[0]
    # MinCount/MaxCount must be exactly 1 — never let a bug spawn many.
    assert call["MinCount"] == 1
    assert call["MaxCount"] == 1
    assert call["LaunchTemplate"] == {
        "LaunchTemplateName": "qpress-sam-gpu-worker",
        "Version": "$Default",
    }


@pytest.mark.asyncio
async def test_terminated_workers_are_ignored():
    """A 'terminated' worker doesn't count as running — boot a new one."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_existing_workers("terminated", "shutting-down")
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "launched"
    assert len(ec2.run_calls) == 1


@pytest.mark.asyncio
async def test_describe_instances_filters_on_project_and_role_tags():
    """The describe call must filter on Project=qpress-sam AND Role=worker."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    lock = FakeAdvisoryLock()

    await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert len(ec2.describe_calls) == 1
    filters = ec2.describe_calls[0]["Filters"]
    # Build a name->values map for assertion.
    name_to_values = {f["Name"]: set(f["Values"]) for f in filters}
    assert "qpress-sam" in name_to_values["tag:Project"]
    assert "worker" in name_to_values["tag:Role"]
    # State filter must include the live states only.
    assert {"pending", "running"}.issubset(name_to_values["instance-state-name"])


@pytest.mark.asyncio
async def test_lock_already_held_makes_call_a_noop():
    """If another caller holds the advisory lock, we skip the launch attempt."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    lock = FakeAdvisoryLock()
    lock.force_held()  # someone else owns the boot window

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "noop"
    assert result.reason == "boot_window_locked"
    # No describe, no run.
    assert ec2.describe_calls == []
    assert ec2.run_calls == []
    # We still tried to acquire (and the FakeAdvisoryLock returned False);
    # we never call release because we never acquired.
    assert lock.acquire_calls == 1
    assert lock.release_calls == 0


@pytest.mark.asyncio
async def test_concurrent_calls_only_boot_once():
    """Two concurrent ensure_worker_running calls → exactly one RunInstances."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    # Single shared lock state, but the two callers race for it. The first
    # acquires, runs the boot, and the second sees the lock held → noop.
    # We simulate that by having the first call hold the lock until both
    # have entered ensure_worker_running.

    class GatedLock(FakeAdvisoryLock):
        def __init__(self) -> None:
            super().__init__()
            self._gate = asyncio.Event()
            self._first_acquired = asyncio.Event()

        async def acquire(self) -> bool:
            self.acquire_calls += 1
            if self._holder_count > 0:
                return False
            self._holder_count = 1
            # First acquire: signal that we're in the critical section, then
            # wait for the test to release us.
            if self.acquire_calls == 1:
                self._first_acquired.set()
                await self._gate.wait()
            return True

        async def release(self) -> None:
            self.release_calls += 1
            self._holder_count = max(0, self._holder_count - 1)

    lock = GatedLock()

    async def attempt():
        return await launcher.ensure_worker_running(
            ec2_client_factory=lambda: ec2,
            advisory_lock=lock,
        )

    t1 = asyncio.create_task(attempt())
    # Wait until t1 has the lock and is suspended inside acquire().
    await lock._first_acquired.wait()
    t2 = asyncio.create_task(attempt())
    # Give t2 a chance to call acquire and bounce off the held lock.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Now release t1.
    lock._gate.set()
    r1, r2 = await asyncio.gather(t1, t2)

    actions = sorted([r1.action, r2.action])
    assert actions == ["launched", "noop"]
    # Exactly one boot happened.
    assert len(ec2.run_calls) == 1


@pytest.mark.asyncio
async def test_insufficient_capacity_raises_typed_error():
    """Spot capacity exhausted → GpuCapacityUnavailable, not raw ClientError."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_run_error(
        "InsufficientInstanceCapacity",
        "We currently do not have sufficient g6e.xlarge capacity in the AZ.",
    )
    lock = FakeAdvisoryLock()

    with pytest.raises(launcher.GpuCapacityUnavailable) as exc_info:
        await launcher.ensure_worker_running(
            ec2_client_factory=lambda: ec2,
            advisory_lock=lock,
        )

    # Caller-friendly message — used in the toast.
    assert "capacity" in str(exc_info.value).lower()
    # Lock must be released even on error.
    assert lock.release_calls == 1


@pytest.mark.asyncio
async def test_other_client_errors_propagate():
    """Non-capacity ClientErrors (e.g. UnauthorizedOperation) bubble up as-is."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_run_error("UnauthorizedOperation", "You are not authorized.")
    lock = FakeAdvisoryLock()

    with pytest.raises(ClientError):
        await launcher.ensure_worker_running(
            ec2_client_factory=lambda: ec2,
            advisory_lock=lock,
        )
    assert lock.release_calls == 1


@pytest.mark.asyncio
async def test_default_factory_uses_boto3_ec2_client(monkeypatch):
    """When no factory is injected, the launcher builds a real boto3 client.

    We don't actually call AWS — we patch boto3.client to return our fake.
    This guards against accidentally hard-coding the factory path during
    refactors.
    """
    import boto3

    from flake_analysis.worker import launcher

    captured: dict[str, Any] = {}
    fake_ec2 = FakeEc2Client()
    fake_ec2.set_existing_workers("running")  # keep this test minimal — noop path

    def fake_boto3_client(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return fake_ec2

    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    lock = FakeAdvisoryLock()
    result = await launcher.ensure_worker_running(advisory_lock=lock)

    assert result.action == "noop"
    assert captured["name"] == "ec2"
    # us-east-2 is hard-coded as that's where qpress-sam-gpu-worker lives.
    assert captured["kwargs"].get("region_name") == "us-east-2"


# ---------------------------------------------------------------------------
# T7 — On-demand fallback when spot capacity is exhausted
# ---------------------------------------------------------------------------


def test_launch_one_falls_back_to_on_demand_when_spot_capacity_unavailable():
    """T7n: when ALL three AZs reject spot with capacity errors,
    _launch_one rotates to on-demand and accepts the first AZ that
    returns an instance. NetworkInterfaces.SubnetId is overridden
    per-call to bypass the LT's pinned single-AZ subnet."""
    from flake_analysis.worker.launcher import _launch_one, SUBNETS_BY_AZ

    capacity_err = ClientError(
        {"Error": {"Code": "InsufficientInstanceCapacity",
                   "Message": "Insufficient capacity"}},
        "RunInstances",
    )

    calls: list[dict] = []

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            calls.append(kwargs)
            # Spot in all three AZs (calls 1-3) refuse, then first
            # on-demand call (call 4 = us-east-2a on-demand) succeeds.
            if self.attempt <= 3:
                raise capacity_err
            return {"Instances": [{"InstanceId": "i-on-demand-test"}]}

    ec2 = _FakeEc2()
    instance_id = _launch_one(ec2)

    assert instance_id == "i-on-demand-test"
    assert len(calls) == 4

    # All four calls override NetworkInterfaces.SubnetId per-AZ.
    subnets_seen = [c["NetworkInterfaces"][0]["SubnetId"] for c in calls]
    expected_subnets = list(SUBNETS_BY_AZ.values())  # 2a, 2b, 2c
    # Calls 1-3 are spot in 2a, 2b, 2c (in dict order).
    assert subnets_seen[:3] == expected_subnets
    # Call 4 is on-demand starting at 2a again.
    assert subnets_seen[3] == expected_subnets[0]

    # Calls 1-3: spot (no InstanceMarketOptions override).
    for c in calls[:3]:
        assert "InstanceMarketOptions" not in c
    # Call 4: on-demand (explicit empty market options).
    assert calls[3]["InstanceMarketOptions"] == {}


def test_launch_one_raises_capacity_error_when_full_ladder_fails():
    """T7q: when every instance type × AZ × market combination is
    refused, _launch_one raises GpuCapacityUnavailable after exactly
    `len(INSTANCE_TYPE_LADDER) × 6` attempts (3 AZs × 2 markets per
    type)."""
    from flake_analysis.worker.launcher import (
        _launch_one,
        GpuCapacityUnavailable,
        INSTANCE_TYPE_LADDER,
    )

    capacity_err = ClientError(
        {"Error": {"Code": "InsufficientInstanceCapacity",
                   "Message": "Insufficient capacity"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            raise capacity_err

    ec2 = _FakeEc2()
    with pytest.raises(GpuCapacityUnavailable):
        _launch_one(ec2)

    assert ec2.attempt == len(INSTANCE_TYPE_LADDER) * 6


def test_launch_one_falls_through_ladder_to_smaller_gpu():
    """T7q: when the largest GPU type is fully drought across all AZs
    and markets (6 refusals), _launch_one steps down to the next
    instance type. First successful attempt wins."""
    from flake_analysis.worker.launcher import (
        _launch_one,
        INSTANCE_TYPE_LADDER,
    )

    capacity_err = ClientError(
        {"Error": {"Code": "InsufficientInstanceCapacity",
                   "Message": "Insufficient capacity"}},
        "RunInstances",
    )

    calls: list[dict] = []

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            calls.append(kwargs)
            # First instance type (g6e.48xlarge) — all 6 attempts refuse.
            # Second instance type, first attempt — succeeds.
            if self.attempt <= 6:
                raise capacity_err
            return {"Instances": [{"InstanceId": "i-fallback-test"}]}

    ec2 = _FakeEc2()
    instance_id = _launch_one(ec2)

    assert instance_id == "i-fallback-test"
    assert ec2.attempt == 7

    # First 6 calls: largest type
    for call in calls[:6]:
        assert call["InstanceType"] == INSTANCE_TYPE_LADDER[0]
    # Call 7: second type
    assert calls[6]["InstanceType"] == INSTANCE_TYPE_LADDER[1]


def test_launch_one_propagates_non_capacity_client_errors():
    """Non-capacity ClientErrors (e.g. UnauthorizedOperation, IAM,
    InvalidParameterValue) propagate directly without retry — they
    won't go away by switching to on-demand."""
    from flake_analysis.worker.launcher import _launch_one

    iam_err = ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "Not allowed"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            raise iam_err

    ec2 = _FakeEc2()
    with pytest.raises(ClientError) as exc_info:
        _launch_one(ec2)

    # Failed on first attempt; no on-demand retry
    assert ec2.attempt == 1
    assert exc_info.value.response["Error"]["Code"] == "UnauthorizedOperation"


def test_launch_one_falls_back_to_on_demand_on_max_spot_count_exceeded():
    """Owner directive 2026-06-08: any spot failure → automatic
    on-demand. MaxSpotInstanceCountExceeded happens when AWS still
    holds the spot quota from a recent terminate (~10 min release
    delay). Without this, every back-to-back run fails."""
    from botocore.exceptions import ClientError
    from flake_analysis.worker.launcher import _launch_one

    quota_err = ClientError(
        {"Error": {"Code": "MaxSpotInstanceCountExceeded",
                   "Message": "Spot instance count limit reached"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            if self.attempt == 1:
                raise quota_err
            return {"Instances": [{"InstanceId": "i-on-demand-after-quota"}]}

    ec2 = _FakeEc2()
    instance_id = _launch_one(ec2)
    assert instance_id == "i-on-demand-after-quota"
    assert ec2.attempt == 2


def test_launch_one_falls_back_to_on_demand_on_unsupported_spot():
    """`Unsupported` is returned for spot when an instance type isn't
    available as spot in a given subnet. Should also retry on-demand."""
    from botocore.exceptions import ClientError
    from flake_analysis.worker.launcher import _launch_one

    unsupported_err = ClientError(
        {"Error": {"Code": "Unsupported",
                   "Message": "spot not supported in this AZ"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            if self.attempt == 1:
                raise unsupported_err
            return {"Instances": [{"InstanceId": "i-on-demand-after-unsupported"}]}

    ec2 = _FakeEc2()
    instance_id = _launch_one(ec2)
    assert instance_id == "i-on-demand-after-unsupported"
    assert ec2.attempt == 2


def test_launch_one_falls_back_on_spot_max_price_too_low():
    """SpotMaxPriceTooLow is also a spot-only failure."""
    from botocore.exceptions import ClientError
    from flake_analysis.worker.launcher import _launch_one

    err = ClientError(
        {"Error": {"Code": "SpotMaxPriceTooLow",
                   "Message": "bid below current price"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            if self.attempt == 1:
                raise err
            return {"Instances": [{"InstanceId": "i-od-after-low-bid"}]}

    ec2 = _FakeEc2()
    assert _launch_one(ec2) == "i-od-after-low-bid"
    assert ec2.attempt == 2


# ---------------------------------------------------------------------------
# T7 — On-demand error tolerance (bug fix 2026-06-12)
# ---------------------------------------------------------------------------


def test_launch_one_continues_ladder_on_max_spot_count_during_ondemand():
    """Bug fix: MaxSpotInstanceCountExceeded can leak onto on-demand
    calls when recent spot requests haven't cleared. This is a transient
    account-level limit that should NOT kill the ladder — step down to
    the next instance type.

    Observed 2026-06-12: g6e.48xlarge spot exhausted (3 AZs), then first
    on-demand call raised MaxSpotInstanceCountExceeded and killed the
    ladder instead of trying 24xlarge."""
    from flake_analysis.worker.launcher import (
        _launch_one,
        INSTANCE_TYPE_LADDER,
    )

    quota_err = ClientError(
        {"Error": {"Code": "MaxSpotInstanceCountExceeded",
                   "Message": "Max spot instance count exceeded"}},
        "RunInstances",
    )

    calls: list[dict] = []

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            calls.append(kwargs)
            # First instance type (48xlarge): spot 3 AZs fail (1-3),
            # then all 3 on-demand AZs raise the quota error (4-6).
            # Should step down to next tier instead of propagating.
            if self.attempt <= 3:
                # Spot capacity error
                raise ClientError(
                    {"Error": {"Code": "InsufficientInstanceCapacity",
                               "Message": "no capacity"}},
                    "RunInstances",
                )
            if 4 <= self.attempt <= 6:
                # On-demand quota leak in all AZs — should continue ladder
                raise quota_err
            # Succeed on first attempt of next tier (spot)
            return {"Instances": [{"InstanceId": "i-fallback-tier"}]}

    ec2 = _FakeEc2()
    instance_id = _launch_one(ec2)

    # Should have stepped down to next tier (not raised)
    assert instance_id == "i-fallback-tier"
    # Attempts: 48xlarge spot×3 + 48xlarge on-demand×3 (all failed) +
    # 24xlarge spot×1 (succeeds)
    assert ec2.attempt == 7
    # Verify the ladder progressed to the 2nd tier on attempt 7
    assert calls[6]["InstanceType"] == INSTANCE_TYPE_LADDER[1]


def test_launch_one_propagates_vcpu_limit_exceeded_immediately():
    """VcpuLimitExceeded is a hard account quota ceiling. If the account
    is at its limit, ALL tiers will fail with the same error. Stepping
    down would burn 24 attempts then mask an actionable "request quota
    increase" signal behind a vague GpuCapacityUnavailable. Propagate
    immediately so the operator sees the real error."""
    from flake_analysis.worker.launcher import _launch_one

    vcpu_err = ClientError(
        {"Error": {"Code": "VcpuLimitExceeded",
                   "Message": "Account vCPU limit exceeded"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            # Spot attempts exhaust capacity
            if self.attempt <= 3:
                raise ClientError(
                    {"Error": {"Code": "InsufficientInstanceCapacity",
                               "Message": "no capacity"}},
                    "RunInstances",
                )
            # On-demand hits hard quota — must propagate
            raise vcpu_err

    ec2 = _FakeEc2()
    with pytest.raises(ClientError) as exc_info:
        _launch_one(ec2)

    assert exc_info.value.response["Error"]["Code"] == "VcpuLimitExceeded"
    # Should fail on first on-demand attempt (4th total), no ladder descent
    assert ec2.attempt == 4


def test_launch_one_propagates_instance_limit_exceeded_immediately():
    """InstanceLimitExceeded is a hard account instance-count quota.
    Same rationale as VcpuLimitExceeded — propagate immediately for
    operator diagnosis, not masked behind GpuCapacityUnavailable."""
    from flake_analysis.worker.launcher import _launch_one

    limit_err = ClientError(
        {"Error": {"Code": "InstanceLimitExceeded",
                   "Message": "Instance limit for this family"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            if self.attempt <= 3:
                raise ClientError(
                    {"Error": {"Code": "InsufficientInstanceCapacity",
                               "Message": "no capacity"}},
                    "RunInstances",
                )
            raise limit_err

    ec2 = _FakeEc2()
    with pytest.raises(ClientError) as exc_info:
        _launch_one(ec2)

    assert exc_info.value.response["Error"]["Code"] == "InstanceLimitExceeded"
    assert ec2.attempt == 4


def test_launch_one_still_propagates_iam_errors_on_ondemand():
    """Genuine bugs (UnauthorizedOperation, InvalidParameterValue) must
    still propagate immediately on on-demand calls — don't mask real
    misconfigurations."""
    from flake_analysis.worker.launcher import _launch_one

    iam_err = ClientError(
        {"Error": {"Code": "UnauthorizedOperation",
                   "Message": "IAM not allowed"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            # Spot attempts work until we hit on-demand
            if self.attempt <= 3:
                raise ClientError(
                    {"Error": {"Code": "InsufficientInstanceCapacity",
                               "Message": "no capacity"}},
                    "RunInstances",
                )
            # On-demand IAM error — must propagate
            raise iam_err

    ec2 = _FakeEc2()
    with pytest.raises(ClientError) as exc_info:
        _launch_one(ec2)

    assert exc_info.value.response["Error"]["Code"] == "UnauthorizedOperation"
    # Should fail on first on-demand attempt (4th total)
    assert ec2.attempt == 4


def test_launch_one_still_propagates_invalid_parameter_on_ondemand():
    """InvalidParameterValue indicates a bug in our template or override
    logic — must propagate immediately, not continue the ladder."""
    from flake_analysis.worker.launcher import _launch_one

    param_err = ClientError(
        {"Error": {"Code": "InvalidParameterValue",
                   "Message": "Bad subnet ID"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            if self.attempt <= 3:
                raise ClientError(
                    {"Error": {"Code": "InsufficientInstanceCapacity",
                               "Message": "no capacity"}},
                    "RunInstances",
                )
            raise param_err

    ec2 = _FakeEc2()
    with pytest.raises(ClientError) as exc_info:
        _launch_one(ec2)

    assert exc_info.value.response["Error"]["Code"] == "InvalidParameterValue"
    assert ec2.attempt == 4


def test_launch_one_raises_capacity_unavailable_after_full_ladder_exhaustion():
    """When MaxSpotInstanceCountExceeded (or other tolerated error) occurs
    on EVERY attempt across the full ladder (4 tiers × 3 AZs × 2 markets
    = 24 attempts), _launch_one must raise GpuCapacityUnavailable (not
    the raw boto ClientError) to converge to the clean terminal state."""
    from flake_analysis.worker.launcher import (
        _launch_one,
        GpuCapacityUnavailable,
        INSTANCE_TYPE_LADDER,
        SUBNETS_BY_AZ,
    )

    quota_err = ClientError(
        {"Error": {"Code": "MaxSpotInstanceCountExceeded",
                   "Message": "Max spot instance count exceeded"}},
        "RunInstances",
    )

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            # Every single attempt refuses with the tolerated error
            raise quota_err

    ec2 = _FakeEc2()
    with pytest.raises(GpuCapacityUnavailable):
        _launch_one(ec2)

    # Full ladder: 4 tiers × 3 AZs × 2 markets = 24 attempts
    expected_attempts = len(INSTANCE_TYPE_LADDER) * len(SUBNETS_BY_AZ) * 2
    assert ec2.attempt == expected_attempts


def test_launch_one_recovers_mid_ladder_after_tolerated_errors():
    """Tolerated error (MaxSpotInstanceCountExceeded) occurs deeper in
    the ladder (not just tier 1). Tier 1 spot exhausts all AZs with
    capacity errors, tier 1 on-demand hits the tolerated error in all
    AZs, then tier 2 spot SUCCEEDS. Assert it returns the tier-2
    instance id and correct InstanceType."""
    from flake_analysis.worker.launcher import (
        _launch_one,
        INSTANCE_TYPE_LADDER,
    )

    quota_err = ClientError(
        {"Error": {"Code": "MaxSpotInstanceCountExceeded",
                   "Message": "Max spot instance count exceeded"}},
        "RunInstances",
    )

    calls: list[dict] = []

    class _FakeEc2:
        def __init__(self):
            self.attempt = 0

        def run_instances(self, **kwargs):
            self.attempt += 1
            calls.append(kwargs)
            # Tier 1 (48xlarge): spot 3 AZs fail with capacity (1-3)
            if self.attempt <= 3:
                raise ClientError(
                    {"Error": {"Code": "InsufficientInstanceCapacity",
                               "Message": "no capacity"}},
                    "RunInstances",
                )
            # Tier 1 on-demand 3 AZs fail with tolerated error (4-6)
            if 4 <= self.attempt <= 6:
                raise quota_err
            # Tier 2 (24xlarge) spot succeeds on first AZ (attempt 7)
            return {"Instances": [{"InstanceId": "i-tier2-recovery"}]}

    ec2 = _FakeEc2()
    instance_id = _launch_one(ec2)

    assert instance_id == "i-tier2-recovery"
    # Verify ladder progressed to tier 2 and succeeded on first spot attempt
    assert ec2.attempt == 7
    assert calls[6]["InstanceType"] == INSTANCE_TYPE_LADDER[1]
    # Verify it's a spot call (no InstanceMarketOptions override)
    assert "InstanceMarketOptions" not in calls[6]
