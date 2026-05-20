"""Version endpoint per backend design §1.5."""
from __future__ import annotations
from fastapi import APIRouter

router = APIRouter(tags=["version"])

@router.get("/version")
async def version():
    """Return flake_core_version + api_version (v1)."""
    try:
        from flake_analysis import __version__
    except ImportError:
        __version__ = "unknown"

    return {
        "flake_core_version": __version__,
        "api_version": "v1",
    }
