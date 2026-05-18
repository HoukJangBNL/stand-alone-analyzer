"""Param + input hashing for stale detection."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Dict


def params_hash(params: Dict[str, Any]) -> str:
    """SHA256 of canonical JSON of params dict."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_mtime(path: str | Path) -> float | None:
    """Return file mtime as Unix timestamp, or None if file does not exist."""
    p = Path(path)
    if not p.exists():
        return None
    return p.stat().st_mtime


def dir_mtime_max(directory: str | Path) -> float | None:
    """Return max mtime of all files under directory (recursive), or None if empty."""
    p = Path(directory)
    if not p.exists() or not p.is_dir():
        return None
    mtimes = [f.stat().st_mtime for f in p.rglob("*") if f.is_file()]
    return max(mtimes) if mtimes else None
