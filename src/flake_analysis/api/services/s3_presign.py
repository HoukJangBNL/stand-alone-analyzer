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
