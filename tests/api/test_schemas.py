import pytest
from flake_analysis.api.schemas.projects import (
    CreateProjectRequest,
    ProjectHandle,
)
from flake_analysis.api.schemas.data import ManifestModel, StepEntryModel


def test_create_project_request_shape():
    """W10-C: CreateProjectRequest takes name + optional description."""
    req = CreateProjectRequest(name="proj1")
    assert req.name == "proj1"
    assert req.description is None


def test_manifest_model_shape():
    """ManifestModel mirrors state/manifest.py::Manifest."""
    m = ManifestModel(version=1, steps={})
    assert m.version == 1
    assert m.steps == {}
