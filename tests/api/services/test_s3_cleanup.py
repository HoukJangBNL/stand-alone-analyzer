"""Tests for s3_cleanup.delete_prefix using moto mock_aws."""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from flake_analysis.api.services.s3_cleanup import delete_prefix


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        yield "test-bucket"


def _put(bucket: str, key: str, body: bytes = b"x") -> None:
    boto3.client("s3", region_name="us-east-2").put_object(
        Bucket=bucket, Key=key, Body=body
    )


def _list(bucket: str, prefix: str) -> list[str]:
    resp = boto3.client("s3", region_name="us-east-2").list_objects_v2(
        Bucket=bucket, Prefix=prefix
    )
    return [obj["Key"] for obj in resp.get("Contents", [])]


def test_delete_prefix_removes_all_objects_under_prefix(s3_bucket):
    _put(s3_bucket, "dev/scans/42/images/a.png")
    _put(s3_bucket, "dev/scans/42/images/b.png")
    _put(s3_bucket, "dev/scans/42/manifest.json")

    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/42/")

    assert deleted == 3
    assert _list(s3_bucket, "dev/scans/42/") == []


def test_delete_prefix_does_not_touch_sibling_prefixes(s3_bucket):
    _put(s3_bucket, "dev/scans/42/images/a.png")
    _put(s3_bucket, "dev/scans/43/images/a.png")
    _put(s3_bucket, "dev/scans/420/images/a.png")  # numeric prefix collision guard

    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/42/")

    assert deleted == 1
    assert _list(s3_bucket, "dev/scans/43/") == ["dev/scans/43/images/a.png"]
    assert _list(s3_bucket, "dev/scans/420/") == ["dev/scans/420/images/a.png"]


def test_delete_prefix_returns_zero_when_nothing_to_delete(s3_bucket):
    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/999/")
    assert deleted == 0


def test_delete_prefix_handles_more_than_1000_objects(s3_bucket):
    """delete_objects has a 1000-key limit per call — helper must page."""
    for i in range(1050):
        _put(s3_bucket, f"dev/scans/42/images/{i:04d}.png")

    deleted = delete_prefix(bucket=s3_bucket, prefix="dev/scans/42/")

    assert deleted == 1050
    assert _list(s3_bucket, "dev/scans/42/") == []
