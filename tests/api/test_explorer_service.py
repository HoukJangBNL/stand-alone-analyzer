"""Explorer service tests — peek-raw size, server-side Y-flip, 60×60 cap."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from flake_analysis.api.errors import ParamsInvalid
from flake_analysis.api.services.explorer_service import (
    build_tile_manifest,
)


def _write_raw_image(folder: Path, stem: str, w: int = 80, h: int = 60) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    Image.fromarray(arr).save(folder / f"{stem}.png")


def _write_thumbnail(folder: Path, lod: int, stem: str, w: int, h: int) -> None:
    lod_dir = folder / f"lod{lod}"
    lod_dir.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    Image.fromarray(arr).save(lod_dir / f"{stem}.webp")


def _write_minimal_manifest_for_explorer(folder: Path, image_id_to_name: dict[int, str]) -> None:
    """Write the minimal manifest.json fields explorer_service reads."""
    raw_dir = folder / "raw"
    cache_dir = folder / "00_thumbnails"
    manifest = {
        "version": 1,
        "analysis_folder": str(folder),
        "raw_images_dir": str(raw_dir),
        "thumbnails_cache_dir": str(cache_dir),
        "annotations_path": str(folder / "annotations.json"),
        "steps": {
            "thumbnails": {
                "completed_at": "2026-05-21T00:00:00Z",
                "params": {},
                "params_hash": "thumb_hash",
                "input_hashes": {},
                "outputs": {
                    "index_json": "00_thumbnails/index.json",
                },
            },
        },
        "image_id_to_stem": image_id_to_name,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))


def _write_thumb_index(folder: Path, lod_sizes: dict[str, list[int]]) -> None:
    cache = folder / "00_thumbnails"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": lod_sizes,
        "signature": ["sig0", "sig1"],
    }))


def test_build_tile_manifest_y_flips_via_row_field(tmp_path: Path):
    """iy=0 → bottom (highest row index), iy=max → top (row=0)."""
    raw = tmp_path / "raw"
    image_id_to_name = {0: "ix000_iy000", 1: "ix000_iy002", 2: "ix001_iy001"}
    for stem in image_id_to_name.values():
        _write_raw_image(raw, stem, w=80, h=60)
    _write_thumb_index(tmp_path, {"0": [64, 48], "1": [192, 144], "2": [480, 360]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    for lod, (w, h) in [(0, (64, 48)), (1, (192, 144)), (2, (480, 360))]:
        for stem in image_id_to_name.values():
            _write_thumbnail(tmp_path / "00_thumbnails", lod, stem, w, h)

    m = build_tile_manifest(tmp_path)
    assert m.grid_w == 2
    assert m.grid_h == 3
    by_stem = {t.stem: t for t in m.tiles}
    # iy=0 → row = grid_h - 1 = 2
    assert by_stem["ix000_iy000"].row == 2
    # iy=2 → row = 0
    assert by_stem["ix000_iy002"].row == 0
    # iy=1 → row = 1
    assert by_stem["ix001_iy001"].row == 1


def test_build_tile_manifest_peeks_raw_size_via_pillow(tmp_path: Path):
    """width_px/height_px come from PIL once per stem, then are cached in the manifest."""
    raw = tmp_path / "raw"
    image_id_to_name = {0: "ix000_iy000"}
    _write_raw_image(raw, "ix000_iy000", w=2048, h=1536)
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    _write_thumbnail(tmp_path / "00_thumbnails", 0, "ix000_iy000", 64, 48)

    m = build_tile_manifest(tmp_path)
    assert m.tiles[0].width_px == 2048
    assert m.tiles[0].height_px == 1536


def test_build_tile_manifest_carries_signature_and_params_hash(tmp_path: Path):
    raw = tmp_path / "raw"
    image_id_to_name = {0: "ix000_iy000"}
    _write_raw_image(raw, "ix000_iy000")
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    _write_thumbnail(tmp_path / "00_thumbnails", 0, "ix000_iy000", 64, 48)

    m = build_tile_manifest(tmp_path)
    assert m.signature == ["sig0", "sig1"]
    assert m.params_hash == "thumb_hash"


def test_build_tile_manifest_rejects_grid_over_60x60(tmp_path: Path):
    """Pinned decision #7: 60×60 cap."""
    raw = tmp_path / "raw"
    image_id_to_name = {i: f"ix{i:03d}_iy000" for i in range(61)}
    for stem in image_id_to_name.values():
        _write_raw_image(raw, stem)
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    for stem in image_id_to_name.values():
        _write_thumbnail(tmp_path / "00_thumbnails", 0, stem, 64, 48)

    with pytest.raises(ParamsInvalid):
        build_tile_manifest(tmp_path)


def test_build_tile_manifest_skips_missing_thumbnails_for_unparseable_names(tmp_path: Path):
    """Names that don't match ix###_iy### use the divmod fallback layout."""
    raw = tmp_path / "raw"
    image_id_to_name = {0: "weird_name_0", 1: "weird_name_1"}
    for stem in image_id_to_name.values():
        _write_raw_image(raw, stem)
    _write_thumb_index(tmp_path, {"0": [64, 48]})
    _write_minimal_manifest_for_explorer(tmp_path, image_id_to_name)
    for stem in image_id_to_name.values():
        _write_thumbnail(tmp_path / "00_thumbnails", 0, stem, 64, 48)

    m = build_tile_manifest(tmp_path)
    # Fallback: 2 images → grid_w=2, grid_h=1
    assert m.grid_w * m.grid_h >= 2
    assert {t.stem for t in m.tiles} == {"weird_name_0", "weird_name_1"}


def _write_clustering_and_proximity(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    (folder / "05_domain_proximity").mkdir(parents=True, exist_ok=True)
    labels = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "thin", "size": 3, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "thick", "size": 2, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"10": 0, "11": 0, "12": 1, "20": 1, "21": 0},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(json.dumps(labels))
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "cluster_id": [0, 0, 1, 1, 0],
        "posterior_p": [0.9, 0.8, 0.85, 0.7, 0.95],
    }).to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "flake_id":  [100, 100, 100, 200, 200],
        "flake_size": [3, 3, 3, 2, 2],
        "image_id":  [0, 0, 0, 1, 1],
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)


def test_build_flake_table_no_filter_returns_all(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=[],
                           size_min=None, size_max=None)
    assert len(df) == 2
    assert set(df["flake_id"].tolist()) == {100, 200}


def test_build_flake_table_include_filter_keeps_matching_only(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=["thick"], exclude_labels=[],
                           size_min=None, size_max=None)
    # Flake 100 has cluster set {thin, thick}; flake 200 has {thick, thin} too.
    # Both pass include={thick}.
    assert set(df["flake_id"].tolist()) == {100, 200}


def test_build_flake_table_exclude_filter_drops_matching(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=["thick"],
                           size_min=None, size_max=None)
    # Both flakes contain "thick" → both excluded.
    assert df.empty


def test_build_flake_table_size_min_max(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=[],
                           size_min=3, size_max=3)
    assert df["flake_id"].tolist() == [100]


def test_build_flake_table_size_max_only(tmp_path: Path):
    from flake_analysis.api.services.explorer_service import build_flake_table
    _write_clustering_and_proximity(tmp_path)
    df = build_flake_table(tmp_path,
                           include_labels=[], exclude_labels=[],
                           size_min=None, size_max=2)
    assert df["flake_id"].tolist() == [200]
