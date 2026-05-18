"""Smoke test for background pipeline wrapper. Uses tiny fixture."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from flake_analysis.pipeline.background import run_background_step
from flake_analysis.state.manifest import load_manifest


def _create_fixture(tmp: Path, n: int = 5) -> Path:
    raw_dir = tmp / "raw_images"
    raw_dir.mkdir()
    rng = np.random.default_rng(0)
    for i in range(n):
        arr = rng.integers(0, 256, size=(50, 50, 3), dtype=np.uint8)
        Image.fromarray(arr).save(raw_dir / f"img_{i:03d}.png")
    return raw_dir


def test_run_background_step_writes_manifest_and_npy():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        raw_dir = _create_fixture(tmp, n=5)
        analysis = tmp / "analysis"
        analysis.mkdir()

        result = run_background_step(
            raw_images_dir=str(raw_dir),
            analysis_folder=str(analysis),
            seed=0,
            max_images=5,
        )

        # background.npy created
        bg_path = analysis / "01_background" / "background.npy"
        assert bg_path.exists(), "background.npy not written"

        # result dict has expected keys
        assert result["output_path"] == str(bg_path)
        assert result["shape"] is not None

        # manifest updated
        m = load_manifest(str(analysis))
        assert "background" in m.steps
        assert m.steps["background"].completed_at is not None
        assert (
            m.steps["background"].outputs["background_npy"]
            == "01_background/background.npy"
        )
        # params recorded
        assert m.steps["background"].params["seed"] == 0
        assert m.steps["background"].params["max_images"] == 5
        # params_hash present
        assert m.steps["background"].params_hash is not None
        assert m.steps["background"].params_hash.startswith("sha256:")
