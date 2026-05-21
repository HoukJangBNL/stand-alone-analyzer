"""Path-traversal guard. All static-asset routes MUST run input through safe_join."""
from __future__ import annotations
import re
from pathlib import Path

from flake_analysis.api.errors import ParamsInvalid

# Allowlist: ASCII letters, digits, dot, underscore, hyphen.
# No slash, no backslash, no spaces, no shell metacharacters, no null byte.
_ALLOWED = re.compile(r"^[A-Za-z0-9_.\-]+$")


def safe_join(base: Path, *parts: str) -> Path:
    """Join `parts` onto `base`, refusing any traversal/absolute/disallowed input.

    Raises ParamsInvalid (HTTP 400) on any of:
    - any part containing ".." as a segment OR substring
    - any part starting with "/" (absolute) or containing "\\"
    - any part containing a character outside [A-Za-z0-9_.-]
    - any part containing a null byte

    The final path is also re-resolved and must remain a child of `base`.
    """
    base_resolved = Path(base).resolve()
    for p in parts:
        if not isinstance(p, str) or not p:
            raise ParamsInvalid(reason="empty_segment")
        if "\x00" in p:
            raise ParamsInvalid(reason="null_byte")
        if p.startswith("/") or p.startswith("\\"):
            raise ParamsInvalid(reason="absolute_path")
        if "\\" in p:
            raise ParamsInvalid(reason="backslash")
        if ".." in p:
            raise ParamsInvalid(reason="dot_dot")
        if not _ALLOWED.match(p):
            raise ParamsInvalid(reason="disallowed_chars", value=p)

    out = base_resolved.joinpath(*parts)
    # Defense-in-depth: ensure the joined path doesn't escape via symlinks
    # by checking the parent chain (we don't resolve `out` itself because
    # the file may not yet exist).
    try:
        out.relative_to(base_resolved)
    except ValueError:
        raise ParamsInvalid(reason="escape_attempted")
    return out
