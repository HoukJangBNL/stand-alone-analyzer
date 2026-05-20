"""Smoke test for the thumbnails LOD pipeline wrapper.

Generates a tiny raw_images fixture, runs the wrapper, and asserts:

* per-LOD WebP thumbnails are written to ``00_thumbnails/lod{0,1,2}/``
* ``index.json`` is well-formed and lists every image
* the manifest gets a ``thumbnails`` step entry
* a second run is fully cached (n_skipped == n_images)

v0.2.16 adds a local-disk cache redirect for network-mount projects;
the redirect test below exercises the env-var opt-in path so it
runs on developer laptops.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from flake_analysis.core.pipeline.thumbnails import (
    LOD_SIZES,
    MAX_LOD,
    _LOCAL_CACHE_ENV,
    _local_cache_dir_for,
    _should_redirect_to_local_cache,
)
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


def test_lod_pyramid_constants():
    # v0.2.15 spec: 3 cached LODs (lod0/1/2), raw is implicit lod3.
    assert set(LOD_SIZES.keys()) == {0, 1, 2}
    assert LOD_SIZES[0] == (64, 40)
    assert LOD_SIZES[1] == (192, 120)
    assert LOD_SIZES[2] == (480, 300)
    assert MAX_LOD == 3


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


def test_should_redirect_volumes_path(monkeypatch):
    # Real ``/Volumes/...`` paths usually don't exist on CI/laptops;
    # we just check the predicate's string-prefix logic. The path
    # doesn't have to resolve — ``_should_redirect_to_local_cache``
    # falls back to the input string when ``resolve()`` fails.
    monkeypatch.delenv(_LOCAL_CACHE_ENV, raising=False)
    fake = Path("/Volumes/QPressDataShare/proj/00_thumbnails")
    assert _should_redirect_to_local_cache(fake) is True


def test_should_redirect_env_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv(_LOCAL_CACHE_ENV, "1")
    # Local tmp dir wouldn't trigger by path, only by env-var.
    assert _should_redirect_to_local_cache(tmp_path) is True


def test_run_thumbnails_local_cache_redirect(monkeypatch, tmp_path):
    """End-to-end: env-var opt-in routes WebPs to local cache.

    Asserts:
      * ``index.json["cache_dir"]`` is populated and points at the
        per-analysis-folder cache directory
      * Every per-entry ``outputs[lod{N}]`` resolves under
        ``cache_dir`` (not the analysis folder)
      * The analysis-folder ``00_thumbnails/lod{N}/`` subfolders are
        NOT created (only ``index.json`` lives there)
      * The ``cache_dir`` flows through to the wrapper return value
    """
    # Redirect the cache root onto tmp so the test never touches
    # ``~/.cache/...``.
    monkeypatch.setenv(_LOCAL_CACHE_ENV, "1")
    fake_root = tmp_path / "fake_cache_root"
    monkeypatch.setattr(
        "flake_analysis.core.pipeline.thumbnails._LOCAL_CACHE_ROOT",
        fake_root,
    )

    raw_dir = _create_fixture(tmp_path, n=3)
    analysis = tmp_path / "analysis"
    analysis.mkdir()

    result = run_thumbnails_step(
        analysis_folder=str(analysis),
        raw_images_dir=str(raw_dir),
    )

    cache_dir = result.get("cache_dir")
    assert cache_dir is not None, (
        "cache_dir should be populated when redirect fires"
    )
    cache_path = Path(cache_dir)
    assert cache_path.exists(), "cache directory should be created"
    assert str(cache_path).startswith(str(fake_root)), (
        f"cache should live under monkeypatched root, got {cache_path}"
    )
    # Per-analysis-folder hashed subdir lives under the patched root
    # with the analysis-folder hash as the leaf.
    expected_cache = _local_cache_dir_for(analysis / "00_thumbnails")
    assert cache_path.resolve() == expected_cache.resolve()

    # Index file is in the analysis folder + carries cache_dir.
    out_root = analysis / "00_thumbnails"
    index_path = out_root / "index.json"
    assert index_path.exists()
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    assert idx.get("cache_dir") == str(cache_path)

    # Every per-entry path resolves under the cache, not the
    # analysis folder.
    for entry in idx["entries"]:
        for lod_key, rel in entry["outputs"].items():
            resolved = cache_path / rel
            assert resolved.exists(), (
                f"thumbnail {lod_key} for {entry['raw_name']} "
                f"missing at {resolved}"
            )

    # Analysis-folder lod{N}/ subfolders should NOT have been
    # created — that's the whole point of the redirect.
    for lod in (0, 1, 2):
        assert not (out_root / f"lod{lod}").exists(), (
            f"analysis-folder lod{lod}/ should be empty when "
            "local cache is active"
        )

    # Cache subfolders should exist + carry the WebPs.
    for lod in (0, 1, 2):
        d = cache_path / f"lod{lod}"
        assert d.is_dir()
        assert len(list(d.glob("*.webp"))) == 3
