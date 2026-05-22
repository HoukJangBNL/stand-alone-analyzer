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
