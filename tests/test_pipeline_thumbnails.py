"""Smoke test for the thumbnails LOD pipeline wrapper.

Generates a tiny raw_images fixture, runs the wrapper, and asserts:

* per-LOD WebP thumbnails are written to ``00_thumbnails/lod{0,1,2}/``
* ``index.json`` is well-formed and lists every image
* the manifest gets a ``thumbnails`` step entry
* a second run is fully cached (n_skipped == n_images)

The wrapper-level test stays scoped to the public
``flake_analysis.pipeline.thumbnails.run_thumbnails_step`` surface;
LOD constants, the local-disk cache redirect heuristic, and other
core-internal mechanics are tested separately in
``tests/core/test_thumbnails_internals.py``.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from flake_analysis.pipeline.thumbnails import run_thumbnails_step
from flake_analysis.state.manifest import load_manifest


def _create_fixture(tmp: Path, n: int = 4) -> Path:
    raw_dir = tmp / "raw_images"
    raw_dir.mkdir()
    rng = np.random.default_rng(0)
    for i in range(n):
        arr = rng.integers(0, 256, size=(120, 192, 3), dtype=np.uint8)
        # Use the ix###_iy### naming the Explorer mosaic relies on.
        Image.fromarray(arr).save(raw_dir / f"ix000_iy{i:03d}.png")
    return raw_dir


def test_run_thumbnails_step_writes_files_and_manifest():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        raw_dir = _create_fixture(tmp, n=4)
        analysis = tmp / "analysis"
        analysis.mkdir()

        result = run_thumbnails_step(
            analysis_folder=str(analysis),
            raw_images_dir=str(raw_dir),
        )

        assert result["n_images"] == 4
        assert result["n_skipped"] == 0
        assert result["n_failed"] == 0

        out_root = analysis / "00_thumbnails"
        index_path = out_root / "index.json"
        assert index_path.exists(), "index.json not written"

        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert index["n_images"] == 4
        assert len(index["entries"]) == 4

        # Every LOD subfolder gets a webp per raw image.
        for lod in (0, 1, 2):
            d = out_root / f"lod{lod}"
            assert d.is_dir()
            assert len(list(d.glob("*.webp"))) == 4

        m = load_manifest(str(analysis))
        assert "thumbnails" in m.steps
        assert m.steps["thumbnails"].completed_at is not None
        assert (
            m.steps["thumbnails"].outputs["index_json"]
            == "00_thumbnails/index.json"
        )
        assert m.steps["thumbnails"].params_hash is not None
        assert m.steps["thumbnails"].params_hash.startswith("sha256:")


def test_run_thumbnails_step_caches_on_second_run():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        raw_dir = _create_fixture(tmp, n=3)
        analysis = tmp / "analysis"
        analysis.mkdir()

        run_thumbnails_step(
            analysis_folder=str(analysis),
            raw_images_dir=str(raw_dir),
        )
        result2 = run_thumbnails_step(
            analysis_folder=str(analysis),
            raw_images_dir=str(raw_dir),
        )
        assert result2["n_images"] == 3
        assert result2["n_skipped"] == 3
        assert result2["n_failed"] == 0
