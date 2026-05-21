"""FastAPI dependencies per backend design §1."""
from __future__ import annotations
import os
from dataclasses import dataclass
from fastapi import Request
from flake_analysis.state.manifest import Manifest, load_manifest

_active_project: str | None = None

DEFAULT_PROJECT_ID = "local"
DEFAULT_ANALYSIS_FOLDER = "/mnt/analysis"


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project identity for a request.

    project_id is the opaque project handle (always "local" in v1).
    analysis_folder is the on-disk path that hosts the project's artifacts.
    """
    project_id: str
    analysis_folder: str


def _resolve_project_id(project_id: str) -> str:
    """Resolve project_id to analysis_folder path. v1: always returns _active_project."""
    global _active_project
    if _active_project is None:
        _active_project = os.environ.get("SAA_ANALYSIS_FOLDER", DEFAULT_ANALYSIS_FOLDER)
    return _active_project


async def get_project_context(request: Request) -> ProjectContext:
    """Resolve the active ProjectContext for this request.

    project_id is read from the path parameter named ``project_id`` when the
    route declares one (e.g. ``/projects/{project_id}/...``); otherwise it
    falls back to ``DEFAULT_PROJECT_ID``. analysis_folder is resolved through
    the same _active_project / SAA_ANALYSIS_FOLDER chain used by get_manifest.
    """
    pid = request.path_params.get("project_id", DEFAULT_PROJECT_ID)
    folder = _resolve_project_id(pid)
    return ProjectContext(project_id=pid, analysis_folder=folder)


async def get_manifest(project_id: str) -> Manifest:
    """Load manifest for project_id (v1: 'local')."""
    analysis_folder = _resolve_project_id(project_id)
    return load_manifest(analysis_folder)
