"""Multi-GPU SAM adapter tests — M3 asset bundle wiring.

Covers the rebuilt ``_run_sam_multi_gpu`` branch that delegates to vendor
``run_amg_v2.run_multi_process`` with M3-local paths (no merged.pt
shortcut). All side effects of vendor calls are stubbed; these tests do
NOT load torch/sam2.

Companion to ``test_sam_engine.py`` (single-GPU path, unchanged).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flake_analysis.core.pipeline import sam as sam_mod


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


# ---------------------------------------------------------------------------
# Path / config building
# ---------------------------------------------------------------------------


def test_build_vendor_config_uses_m3_paths(monkeypatch, tmp_path):
    """``_build_vendor_config`` returns a dict whose paths point at the
    on-instance M3 layout (configurable via ``SAM_M3_ROOT`` env var so
    tests don't depend on /opt/sam being writable)."""
    fake_root = tmp_path / "sam_m3"
    fake_root.mkdir()
    monkeypatch.setenv("SAM_M3_ROOT", str(fake_root))
    # Reload the module-level constant lookup by monkeypatching the
    # attribute the function reads at call time.
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)

    cfg = sam_mod._build_vendor_config()

    # Vendor build_sam2_finetuned reads ckpt_dir + ckpt_file
    assert cfg["ckpt_dir"] == str(fake_root / "sam2_lora")
    assert cfg["ckpt_file"] == "best_model.pth"
    # use_original_sam2 must be False — we want the LoRA build path.
    assert cfg["use_original_sam2"] is False
    # AMG defaults match run_amg_v2.parse_args defaults verbatim.
    assert cfg["points_per_side"] == 48
    assert cfg["points_per_batch"] == 64
    assert cfg["pred_iou_thresh"] == pytest.approx(0.78)
    assert cfg["stability_score_thresh"] == pytest.approx(0.88)
    assert cfg["box_nms_thresh"] == pytest.approx(0.6)
    assert cfg["crop_n_layers"] == 1
    assert cfg["crop_overlap_ratio"] == pytest.approx(512 / 1500)
    assert cfg["output_mode"] == "binary_mask"
    assert cfg["min_mask_region_area"] == 500
    # Side-effect controls — only masks saving is on by default.
    assert cfg["save_masks"] is True
    assert cfg["save_original"] is False
    assert cfg["save_overlays"] is False
    # Flat-enclosure rejection knobs (parser defaults).
    assert cfg["reject_flat_enclosed"] is False
    assert cfg["flatfield_path"] is None


def test_load_and_patch_args_rewrites_prod_paths(tmp_path, monkeypatch):
    """``_load_and_patch_args`` reads the on-disk args.json (prod absolute
    paths inside) and returns a dict whose ``model_dir`` / ``checkpoint``
    / ``config`` fields point at M3-local equivalents — without writing
    to disk."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)

    args_path = fake_root / "sam2_lora" / "args.json"
    src = (FIXTURES / "sam_m3_args.json").read_text()
    args_path.write_text(src)
    on_disk_before = args_path.read_text()

    patched = sam_mod._load_and_patch_args(fake_root / "sam2_lora")

    # Patched in-memory dict points at /opt-style M3 paths.
    assert patched["checkpoint"] == str(fake_root / "sam2.1" / "sam2.1_hiera_l.pt")
    assert patched["config"] == str(
        fake_root / "sam2.1" / "configs" / "sam2.1_hiera_l.yaml"
    )
    assert patched["model_dir"] == str(fake_root)
    # LoRA hyperparams preserved verbatim.
    assert patched["lora_image_encoder_rank"] == 16
    assert patched["lora_alpha"] == 32.0
    assert patched["lora_dropout"] == 0.1
    # On-disk file MUST NOT have been mutated.
    assert args_path.read_text() == on_disk_before


def test_safe_ensure_sam2_importable_preserves_cwd(tmp_path, monkeypatch):
    """The vendor ``ensure_sam2_importable`` does ``os.chdir(sam2_repo)``
    which is a process-wide side effect. Our adapter wrapper must
    save+restore cwd (P1.2 precedent)."""
    cwd_before = os.getcwd()
    sam2_repo = tmp_path / "sam2_repo"
    sam2_repo.mkdir()

    chdir_calls: list = []
    real_chdir = os.chdir

    def fake_vendor_ensure(repo):
        # Mimic the vendor side effect: chdir into the repo.
        real_chdir(str(repo))
        chdir_calls.append(repo)
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))

    monkeypatch.setattr(sam_mod, "_vendor_ensure_sam2_importable", fake_vendor_ensure)

    # Reset the module-level "already-applied" guard so the call actually fires.
    monkeypatch.setattr(sam_mod, "_SAM2_IMPORT_APPLIED", False, raising=False)

    sam_mod._safe_ensure_sam2_importable(sam2_repo)

    assert chdir_calls == [sam2_repo]
    assert os.getcwd() == cwd_before, "cwd must be restored after vendor chdir"


# ---------------------------------------------------------------------------
# Hardware gate routing
# ---------------------------------------------------------------------------


def test_hardware_gate_falls_through_to_vendor_infer_when_one_gpu(
    tmp_path, monkeypatch
):
    """When ``torch.cuda.device_count() < 2``, ``run_sam`` must use the
    single-GPU ``_vendor_infer`` path (unchanged)."""
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"

    # Stub torch presence + device count.
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.device_count.return_value = 1
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    multi_called = MagicMock()
    monkeypatch.setattr(sam_mod, "_run_sam_multi_gpu", multi_called)

    with patch.object(sam_mod, "_vendor_infer") as vinfer:
        vinfer.return_value = {}
        sam_mod.run_sam(
            images_dir=images_dir,
            weights_path=tmp_path / "merged.pt",
            out_dir=out_dir,
            device="cpu",
            progress_callback=None,
        )

    assert vinfer.called, "single-GPU path must call _vendor_infer"
    assert not multi_called.called, "multi-GPU branch must not fire on 1 GPU"


def test_hardware_gate_routes_to_multi_gpu_when_eight(tmp_path, monkeypatch):
    """When 8 GPUs are visible, ``run_sam`` must call
    ``_run_sam_multi_gpu`` and return its summary verbatim."""
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"

    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.device_count.return_value = 8
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    expected = {
        "images": 3,
        "masks_total": 42,
        "errors": 0,
        "per_image": {
            "a.png": {"n_masks": 14, "error": None},
            "b.png": {"n_masks": 14, "error": None},
            "c.png": {"n_masks": 14, "error": None},
        },
    }
    multi = MagicMock(return_value=expected)
    monkeypatch.setattr(sam_mod, "_run_sam_multi_gpu", multi)

    result = sam_mod.run_sam(
        images_dir=images_dir,
        weights_path=tmp_path / "weights",
        out_dir=out_dir,
        device=None,
        progress_callback=None,
    )

    assert multi.called, "8-GPU host must hit the multi-GPU branch"
    assert result == expected
    # Required summary keys (matches single-GPU shape).
    for key in ("images", "masks_total", "errors", "per_image"):
        assert key in result


# ---------------------------------------------------------------------------
# Multi-GPU orchestration
# ---------------------------------------------------------------------------


def test_run_sam_multi_gpu_invokes_vendor_run_multi_process(tmp_path, monkeypatch):
    """End-to-end stub: ``_run_sam_multi_gpu`` must call vendor
    ``run_multi_process`` with the M3-shaped config and translate its
    list-of-dicts result to our summary shape."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)

    # Place args.json so _load_and_patch_args succeeds.
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    for name in ("a.png", "b.png"):
        (images_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Stub vendor-side effects.
    monkeypatch.setattr(
        sam_mod, "_safe_ensure_sam2_importable", MagicMock()
    )
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)

    captured: dict = {}

    def fake_run_multi_process(images, output_dir, config, num_gpus):
        captured["images"] = list(images)
        captured["output_dir"] = output_dir
        captured["config"] = dict(config)
        captured["num_gpus"] = num_gpus
        # Vendor returns List[Dict] with image_info / num_masks / etc.
        return [
            {
                "image_info": {"id": 1, "file_name": "a.png"},
                "annotations": [],
                "num_masks": 7,
                "mask_paths": [],
                "image_path": images_dir / "a.png",
            },
            {
                "image_info": {"id": 2, "file_name": "b.png"},
                "annotations": [],
                "num_masks": 5,
                "mask_paths": [],
                "image_path": images_dir / "b.png",
            },
        ]

    monkeypatch.setattr(sam_mod, "_vendor_run_multi_process", fake_run_multi_process)

    summary = sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=8,
        progress_callback=None,
    )

    # Vendor was called with M3 paths.
    assert captured["num_gpus"] == 8
    cfg = captured["config"]
    assert cfg["ckpt_dir"] == str(fake_root / "sam2_lora")
    assert cfg["ckpt_file"] == "best_model.pth"
    # And the patched args.json took effect (vendor's load_training_args
    # now returns rewritten paths; this is asserted separately via the
    # monkeypatch on load_training_args — see next test).

    # Output translated to our summary shape.
    assert summary == {
        "images": 2,
        "masks_total": 12,
        "errors": 0,
        "per_image": {
            "a.png": {"n_masks": 7, "error": None},
            "b.png": {"n_masks": 5, "error": None},
        },
    }
    # Per-image manifest written.
    manifest = json.loads((out_dir / "per_image_results.json").read_text())
    assert manifest["images"] == 2


def test_run_sam_multi_gpu_patches_vendor_load_training_args(tmp_path, monkeypatch):
    """Inside the multi-GPU call, vendor's ``load_training_args`` must
    return M3-rewritten paths (not the raw prod absolutes from args.json)."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    (images_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    monkeypatch.setattr(sam_mod, "_safe_ensure_sam2_importable", MagicMock())
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )

    # Stub the vendor module surface so production code can monkeypatch
    # ``load_training_args`` on it without dragging real torch/sam2 in.
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)

    seen_args: list[dict] = []

    def fake_run_multi_process(images, output_dir, config, num_gpus):
        # Simulate what vendor would do inside its worker: call the
        # currently-bound ``load_training_args`` and observe the result.
        seen_args.append(fake_vendor.load_training_args(Path(config["ckpt_dir"])))
        return []

    monkeypatch.setattr(sam_mod, "_vendor_run_multi_process", fake_run_multi_process)

    sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=2,
        progress_callback=None,
    )

    assert seen_args, "expected the patched load_training_args to be queryable"
    patched = seen_args[0]
    assert patched["checkpoint"].startswith(str(fake_root))
    assert patched["config"].startswith(str(fake_root))
    # No prod absolute paths leaking through.
    assert "/home2/qpress" not in patched["checkpoint"]
    assert "/home2/qpress" not in patched["config"]

    # After the run, the vendor module's ``load_training_args`` must be
    # restored to the original (no leaked monkeypatch across calls).
    assert fake_vendor.load_training_args is not sam_mod._patched_load_training_args


# ---------------------------------------------------------------------------
# merged_m3 dual-mode routing (#209)
# ---------------------------------------------------------------------------
#
# When ``SAM_MERGED_M3_PATH`` env var is set AND points at a non-empty
# file, the multi-GPU dispatch should prefer vendor's single-``.pt`` build
# path (``build_sam2_from_yaml`` via ``use_original_sam2=True``) over the
# LoRA-runtime path (``build_sam2_finetuned``). The merged_m3 artifact has
# LoRA already folded into the base weights, so per-forward LoRA
# application is unnecessary — recovering the ~3× per-card slowdown
# documented in docs/sam-ops.md §15.
#
# Discovery rules:
#   - env var unset           → LoRA-runtime (existing behavior)
#   - env var set, file 0 B   → LoRA-runtime (corrupted artifact)
#   - env var set, missing    → LoRA-runtime (soft-miss, cluster booted
#                                              before merged_m3 published)
#   - env var set, file >0 B  → merged_m3 (preferred)


def _make_fake_merged_m3(tmp_path: Path) -> Path:
    """Create a non-empty fake merged_m3.pt and return its path."""
    pt = tmp_path / "merged_m3.pt"
    pt.write_bytes(b"\x80\x02" + b"\x00" * 64)  # any non-empty payload
    return pt


def test_resolve_merged_m3_path_returns_none_when_env_unset(tmp_path, monkeypatch):
    """Discovery returns None when ``SAM_MERGED_M3_PATH`` is unset → caller
    falls back to LoRA-runtime."""
    monkeypatch.delenv("SAM_MERGED_M3_PATH", raising=False)
    assert sam_mod._resolve_merged_m3_path() is None


def test_resolve_merged_m3_path_returns_none_when_file_missing(tmp_path, monkeypatch):
    """Discovery returns None when env var is set but the file does not
    exist on disk (soft-miss is normal — cluster may boot before
    merged_m3 has been published to S3)."""
    monkeypatch.setenv("SAM_MERGED_M3_PATH", str(tmp_path / "nonexistent.pt"))
    assert sam_mod._resolve_merged_m3_path() is None


def test_resolve_merged_m3_path_returns_none_when_file_empty(tmp_path, monkeypatch):
    """A 0-byte file is treated as missing — defensive against partial
    downloads or other corruption modes that the userdata SHA256 check
    might miss in edge cases."""
    empty = tmp_path / "merged_m3.pt"
    empty.write_bytes(b"")
    monkeypatch.setenv("SAM_MERGED_M3_PATH", str(empty))
    assert sam_mod._resolve_merged_m3_path() is None


def test_resolve_merged_m3_path_returns_path_when_file_present(tmp_path, monkeypatch):
    """Happy path: env var set + non-empty file → returns the Path."""
    pt = _make_fake_merged_m3(tmp_path)
    monkeypatch.setenv("SAM_MERGED_M3_PATH", str(pt))
    out = sam_mod._resolve_merged_m3_path()
    assert out == pt
    assert out.is_file()


def test_run_sam_multi_gpu_routes_through_merged_m3_when_env_set(
    tmp_path, monkeypatch
):
    """When ``SAM_MERGED_M3_PATH`` is set + file present, the config
    pickled to vendor ``run_multi_process`` must select the
    single-``.pt`` build path (``use_original_sam2=True``) and point
    ``checkpoint`` at the merged_m3 file. The LoRA-runtime keys
    (``ckpt_dir`` / ``ckpt_file``) must be inert under this routing."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )
    # Materialise the yaml the merged_m3 path will reference (we use the
    # M3 bundle's yaml since the userdata only fetches the .pt — the
    # co-located yaml in S3 is informational; the bundle yaml is the
    # source of truth on the worker).
    yaml_path = fake_root / "sam2.1" / "configs" / "sam2.1_hiera_l.yaml"
    yaml_path.write_text("# stub yaml\n")

    merged_m3 = _make_fake_merged_m3(tmp_path)
    monkeypatch.setenv("SAM_MERGED_M3_PATH", str(merged_m3))

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    (images_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    monkeypatch.setattr(sam_mod, "_safe_ensure_sam2_importable", MagicMock())
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)

    captured: dict = {}

    def fake_run_multi_process(images, output_dir, config, num_gpus):
        captured["config"] = dict(config)
        return []

    monkeypatch.setattr(sam_mod, "_vendor_run_multi_process", fake_run_multi_process)

    sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=8,
        progress_callback=None,
    )

    cfg = captured["config"]
    # Routing flag flipped so vendor worker_process_images dispatches to
    # build_sam2_from_yaml (single-.pt path).
    assert cfg["use_original_sam2"] is True, (
        "merged_m3 must route through vendor's single-.pt build path"
    )
    # Single-.pt path reads config_yaml + checkpoint.
    assert cfg["checkpoint"] == str(merged_m3)
    assert cfg["config_yaml"] == str(yaml_path)


def test_run_sam_multi_gpu_routes_through_lora_when_env_unset(tmp_path, monkeypatch):
    """When ``SAM_MERGED_M3_PATH`` is unset, fallback to the existing
    LoRA-runtime path (use_original_sam2=False, ckpt_dir + ckpt_file
    populated, checkpoint/config_yaml left as None)."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )
    monkeypatch.delenv("SAM_MERGED_M3_PATH", raising=False)

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    (images_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    monkeypatch.setattr(sam_mod, "_safe_ensure_sam2_importable", MagicMock())
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)

    captured: dict = {}

    def fake_run_multi_process(images, output_dir, config, num_gpus):
        captured["config"] = dict(config)
        return []

    monkeypatch.setattr(sam_mod, "_vendor_run_multi_process", fake_run_multi_process)

    sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=8,
        progress_callback=None,
    )

    cfg = captured["config"]
    # LoRA-runtime baseline preserved.
    assert cfg["use_original_sam2"] is False
    assert cfg["ckpt_dir"] == str(fake_root / "sam2_lora")
    assert cfg["ckpt_file"] == "best_model.pth"


def test_run_sam_multi_gpu_falls_back_when_merged_m3_file_missing(
    tmp_path, monkeypatch
):
    """``SAM_MERGED_M3_PATH`` set but file does not exist on disk →
    LoRA-runtime fallback. This is the soft-miss path the userdata
    explicitly leaves room for (S3 has no merged_m3 yet)."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )
    # Point at a path that does not exist.
    monkeypatch.setenv("SAM_MERGED_M3_PATH", str(tmp_path / "absent.pt"))

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    (images_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    monkeypatch.setattr(sam_mod, "_safe_ensure_sam2_importable", MagicMock())
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)

    captured: dict = {}

    def fake_run_multi_process(images, output_dir, config, num_gpus):
        captured["config"] = dict(config)
        return []

    monkeypatch.setattr(sam_mod, "_vendor_run_multi_process", fake_run_multi_process)

    sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=8,
        progress_callback=None,
    )

    # Fallback kicked in.
    assert captured["config"]["use_original_sam2"] is False
    assert captured["config"]["ckpt_dir"] == str(fake_root / "sam2_lora")


def test_run_sam_multi_gpu_logs_routing_choice_to_progress_callback(
    tmp_path, monkeypatch
):
    """The dual-mode routing decision must surface in the
    ``progress_callback`` stream so #211 re-measurement can verify the
    new path is being taken from the worker logs alone (since vendor's
    ``mp.spawn`` workers re-import in fresh interpreters and don't see
    parent-process state, the parent-side log + the config dict pickled
    to the workers is the auditable record)."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )
    yaml_path = fake_root / "sam2.1" / "configs" / "sam2.1_hiera_l.yaml"
    yaml_path.write_text("# stub yaml\n")
    merged_m3 = _make_fake_merged_m3(tmp_path)
    monkeypatch.setenv("SAM_MERGED_M3_PATH", str(merged_m3))

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    (images_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    monkeypatch.setattr(sam_mod, "_safe_ensure_sam2_importable", MagicMock())
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)
    monkeypatch.setattr(
        sam_mod, "_vendor_run_multi_process", lambda *a, **kw: []
    )

    messages: list[str] = []

    def cb(pct: float, msg: str) -> None:
        messages.append(msg)

    sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=8,
        progress_callback=cb,
    )

    # At least one message must mention the chosen routing + the merged_m3
    # basename (auditable from logs).
    routing_msgs = [m for m in messages if "merged_m3" in m]
    assert routing_msgs, f"expected merged_m3 routing log; got {messages}"
    assert "merged_m3.pt" in routing_msgs[0]


def test_run_sam_multi_gpu_logs_lora_runtime_fallback_to_progress_callback(
    tmp_path, monkeypatch
):
    """Symmetric to the merged_m3 logging test: the LoRA-runtime fallback
    must also be auditable in the progress_callback stream so a
    re-measurement can prove which path was taken without trawling
    nvidia-smi."""
    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )
    monkeypatch.delenv("SAM_MERGED_M3_PATH", raising=False)

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    (images_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    monkeypatch.setattr(sam_mod, "_safe_ensure_sam2_importable", MagicMock())
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)
    monkeypatch.setattr(
        sam_mod, "_vendor_run_multi_process", lambda *a, **kw: []
    )

    messages: list[str] = []
    sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=8,
        progress_callback=lambda pct, msg: messages.append(msg),
    )

    routing_msgs = [m for m in messages if "lora-runtime" in m]
    assert routing_msgs, f"expected lora-runtime routing log; got {messages}"


def test_run_sam_multi_gpu_config_dict_is_pickle_safe(tmp_path, monkeypatch):
    """Vendor ``run_multi_process`` uses ``mp.get_context("spawn").Pool``
    which pickles the config dict to each worker. The routing decision
    therefore lives in the dict (not in module-level state or
    monkeypatches) so it survives spawn re-import. This test asserts the
    config dict round-trips through ``pickle`` cleanly with the
    merged_m3 keys set."""
    import pickle

    fake_root = tmp_path / "sam_m3"
    (fake_root / "sam2_lora").mkdir(parents=True)
    (fake_root / "sam2.1" / "configs").mkdir(parents=True)
    monkeypatch.setattr(sam_mod, "M3_ROOT", fake_root, raising=True)
    (fake_root / "sam2_lora" / "args.json").write_text(
        (FIXTURES / "sam_m3_args.json").read_text()
    )
    yaml_path = fake_root / "sam2.1" / "configs" / "sam2.1_hiera_l.yaml"
    yaml_path.write_text("# stub yaml\n")
    merged_m3 = _make_fake_merged_m3(tmp_path)
    monkeypatch.setenv("SAM_MERGED_M3_PATH", str(merged_m3))

    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    (images_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    monkeypatch.setattr(sam_mod, "_safe_ensure_sam2_importable", MagicMock())
    monkeypatch.setattr(
        sam_mod, "_resolve_sam2_repo", lambda: fake_root / "sam2_repo_stub"
    )
    fake_vendor = MagicMock()
    fake_vendor.load_training_args = MagicMock(name="orig_loader")
    monkeypatch.setattr(sam_mod, "_vendor_amg_module", lambda: fake_vendor)

    captured_cfg: dict = {}

    def fake_run_multi_process(images, output_dir, config, num_gpus):
        # Round-trip through pickle to simulate spawn-pool serialisation.
        captured_cfg["original"] = config
        captured_cfg["roundtrip"] = pickle.loads(pickle.dumps(config))
        return []

    monkeypatch.setattr(sam_mod, "_vendor_run_multi_process", fake_run_multi_process)

    sam_mod._run_sam_multi_gpu(
        images_dir=images_dir,
        weights_path=fake_root / "sam2_lora" / "best_model.pth",
        out_dir=out_dir,
        n_gpus=8,
        progress_callback=None,
    )

    # Identical after round-trip → spawn workers see the same routing decision.
    assert captured_cfg["roundtrip"] == captured_cfg["original"]
    # Spot-check the routing keys made it.
    assert captured_cfg["roundtrip"]["use_original_sam2"] is True
    assert captured_cfg["roundtrip"]["checkpoint"] == str(merged_m3)
