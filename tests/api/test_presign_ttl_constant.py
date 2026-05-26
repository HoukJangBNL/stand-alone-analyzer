"""SSoT test for the presign TTL constant.

B5 (upload-robustness): the 300-second presign TTL is centralized as
`PRESIGN_TTL_SECONDS` in `s3_presign`. Both the helper's default arg and
the route call sites must reference the constant rather than a magic
literal so future tuning happens in exactly one place.
"""
from __future__ import annotations

import inspect

from flake_analysis.api.routes import scans as scans_route
from flake_analysis.api.services import s3_presign


def test_presign_ttl_constant_is_300_seconds():
    assert s3_presign.PRESIGN_TTL_SECONDS == 300


def test_presign_put_default_uses_ttl_constant():
    """The library default for `expires_in` must be the named constant."""
    sig = inspect.signature(s3_presign.presign_put)
    assert sig.parameters["expires_in"].default == s3_presign.PRESIGN_TTL_SECONDS


def test_scans_route_imports_ttl_constant():
    """Route module must reference the constant rather than re-hardcoding 300."""
    assert scans_route.s3_presign.PRESIGN_TTL_SECONDS == 300
    src = inspect.getsource(scans_route)
    # Both presign call sites should pass the named constant, not a literal.
    assert "expires_in=300" not in src
    assert src.count("PRESIGN_TTL_SECONDS") >= 2
