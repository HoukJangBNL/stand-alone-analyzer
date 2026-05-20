"""Per-project asyncio.Lock registry per backend design §3.2."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from flake_analysis.api.errors import ProjectBusy

_project_locks: dict[str, asyncio.Lock] = {}

def _get_lock(project_id: str) -> asyncio.Lock:
    """Get or create lock for project_id."""
    if project_id not in _project_locks:
        _project_locks[project_id] = asyncio.Lock()
    return _project_locks[project_id]

@asynccontextmanager
async def acquire_project_lock(project_id: str):
    """Acquire per-project lock or raise ProjectBusy immediately if held."""
    lock = _get_lock(project_id)
    if lock.locked():
        raise ProjectBusy(project_id=project_id)

    async with lock:
        yield
