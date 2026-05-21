"""Data endpoint schemas per backend design §1.3."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict

class StepEntryModel(BaseModel):
    """Mirrors state/manifest.py::StepEntry."""
    completed_at: str | None = None
    params: dict = {}
    params_hash: str | None = None
    input_hashes: dict = {}
    outputs: dict[str, str] = {}
    reproducibility: dict = {}

    model_config = ConfigDict(from_attributes=True)

class ManifestModel(BaseModel):
    """Mirrors state/manifest.py::Manifest.

    ``status`` is W2.4-additive: lowercase PipelineStatus value when a DB
    Analysis row backs the project, ``None`` otherwise.
    """
    version: int = 1
    created_at: str | None = None
    raw_images_dir: str | None = None
    annotations_path: str | None = None
    analysis_folder: str | None = None
    flake_core_version: str | None = None
    steps: dict[str, StepEntryModel] = {}
    status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
