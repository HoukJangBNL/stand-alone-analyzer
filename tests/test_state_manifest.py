import json
from pathlib import Path
import tempfile
import pytest

from flake_analysis.state.manifest import (
    Manifest, StepEntry, load_manifest, save_manifest, step_status,
    MANIFEST_VERSION,
)


def test_load_missing_returns_fresh():
    with tempfile.TemporaryDirectory() as tmp:
        m = load_manifest(tmp)
        assert m.version == MANIFEST_VERSION
        assert m.steps == {}


def test_save_then_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        m = Manifest(
            created_at="2026-05-18T10:00:00Z",
            analysis_folder=tmp,
            steps={
                "background": StepEntry(
                    completed_at="2026-05-18T10:05:00Z",
                    params={"seed": 0, "max_images": 100},
                    params_hash="sha256:abc",
                    outputs={"background_npy": "01_background/background.npy"},
                ),
            },
        )
        save_manifest(m, tmp)
        # File exists
        assert (Path(tmp) / "manifest.json").exists()
        # Round-trip
        m2 = load_manifest(tmp)
        assert m2.version == MANIFEST_VERSION
        assert "background" in m2.steps
        assert m2.steps["background"].params_hash == "sha256:abc"


def test_step_status_not_started():
    m = Manifest()
    assert step_status(m, "background") == "not_started"


def test_step_status_done():
    m = Manifest(steps={
        "background": StepEntry(completed_at="2026-05-18T10:00:00Z"),
    })
    assert step_status(m, "background") == "done"


def test_step_status_unknown():
    with pytest.raises(ValueError):
        step_status(Manifest(), "unknown_step")
