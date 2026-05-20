# tests/api/test_mutex.py
import pytest
import asyncio
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.api.errors import ProjectBusy

@pytest.mark.asyncio
async def test_per_project_lock():
    """Lock is acquired per project_id; different projects don't block."""
    async with acquire_project_lock("proj1"):
        with pytest.raises(ProjectBusy):
            async with acquire_project_lock("proj1"):
                pass

        async with acquire_project_lock("proj2"):
            pass

@pytest.mark.asyncio
async def test_lock_released_on_exit():
    """Lock is released after context manager exits."""
    async with acquire_project_lock("proj1"):
        pass

    async with acquire_project_lock("proj1"):
        pass
