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
