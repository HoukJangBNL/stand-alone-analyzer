"""FastAPI dependencies per backend design §1."""
from __future__ import annotations
import os
from flake_analysis.state.manifest import Manifest, load_manifest

_active_project: str | None = None

def _resolve_project_id(project_id: str) -> str:
    """Resolve project_id to analysis_folder path. v1: always returns _active_project."""
    global _active_project
    if _active_project is None:
        _active_project = os.environ.get("SAA_ANALYSIS_FOLDER", "/mnt/analysis")
    return _active_project

async def get_manifest(project_id: str) -> Manifest:
    """Load manifest for project_id (v1: 'local')."""
    analysis_folder = _resolve_project_id(project_id)
    return load_manifest(analysis_folder)
