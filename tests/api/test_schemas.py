import pytest
from flake_analysis.api.schemas.projects import (
    CreateProjectRequest,
    ProjectHandle,
    ValidatePathsRequest,
    PathStatus,
)
from flake_analysis.api.schemas.data import ManifestModel, StepEntryModel

def test_create_project_request_shape():
    """CreateProjectRequest has analysis_folder + optional paths."""
    req = CreateProjectRequest(analysis_folder="/mnt/analysis/proj1")
    assert req.analysis_folder == "/mnt/analysis/proj1"
    assert req.raw_images_dir is None

def test_manifest_model_shape():
    """ManifestModel mirrors state/manifest.py::Manifest."""
    m = ManifestModel(version=1, steps={})
    assert m.version == 1
    assert m.steps == {}
