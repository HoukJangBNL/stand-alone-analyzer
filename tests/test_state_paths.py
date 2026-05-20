from flake_analysis.state.paths import (
    PIPELINE_STEPS, SUBDIRS, ARTIFACTS, step_dir, manifest_path,
)
from pathlib import Path


def test_pipeline_steps_count():
    # v0.2.15 added the ``thumbnails`` LOD pre-render step (00_thumbnails/).
    assert len(PIPELINE_STEPS) == 7


def test_subdirs_match_steps():
    assert set(SUBDIRS.keys()) == set(PIPELINE_STEPS)


def test_artifacts_per_step():
    assert "stats.npz" in ARTIFACTS["domain_stats"]
    assert "labels.json" in ARTIFACTS["clustering"]
    assert "background.npy" in ARTIFACTS["background"]


def test_step_dir():
    p = step_dir("/tmp/run", "background")
    assert p == Path("/tmp/run/01_background")


def test_manifest_path():
    assert manifest_path("/tmp/run") == Path("/tmp/run/manifest.json")


def test_step_dir_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        step_dir("/tmp/run", "no_such_step")
