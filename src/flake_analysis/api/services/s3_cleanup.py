"""S3 batch-delete helper for scan cleanup.

Deletes all objects under a key prefix in pages of 1000 (the AWS API limit
per `delete_objects` call). Trailing slash on the prefix is required so we
don't match e.g. `dev/scans/420/` when asked to clean `dev/scans/42/`.
"""
from __future__ import annotations

import logging
import os

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

_PAGE = 1000


def _client():
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-2")
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def delete_prefix(*, bucket: str, prefix: str) -> int:
    """Delete every object whose key starts with `prefix`. Return count deleted.

    `prefix` must end with `/` to avoid sibling-prefix collisions
    (e.g. `dev/scans/42/` not `dev/scans/42`).
    """
    if not prefix.endswith("/"):
        raise ValueError(f"prefix must end with '/': {prefix!r}")
    client = _client()
    paginator = client.get_paginator("list_objects_v2")
    total = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents") or []
        if not contents:
            continue
        for i in range(0, len(contents), _PAGE):
            batch = contents[i : i + _PAGE]
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in batch]},
            )
            total += len(batch)
    logger.info("s3_cleanup.delete_prefix done", extra={"bucket": bucket, "prefix": prefix, "deleted": total})
    return total
