"""Health endpoint per deployment design §9.3."""
from __future__ import annotations
import os
from fastapi import APIRouter

router = APIRouter(tags=["health"])

@router.get("/health")
async def health():
    """Liveness + SMB reachability check.

    Always returns 200 (never fails liveness) but includes flags
    for storage health so FE can distinguish 'backend up but SMB down'.
    """
    try:
        from flake_analysis import __version__
    except ImportError:
        __version__ = "unknown"

    smb_reachable = os.path.ismount("/mnt/analysis") or os.path.exists("/mnt/analysis")

    return {
        "ok": True,
        "version": __version__,
        "smb_reachable": smb_reachable,
    }
