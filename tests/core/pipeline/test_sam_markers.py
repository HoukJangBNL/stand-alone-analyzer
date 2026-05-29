"""Verify marker:* progress_callback calls fire in correct order across
both single-GPU and multi-GPU branches of run_sam."""
from __future__ import annotations

from pathlib import Path


def _markers(progress_calls: list[tuple[float, str]]) -> list[str]:
    return [m for (_pct, m) in progress_calls if m.startswith("marker:")]


def test_run_sam_single_gpu_emits_three_markers(monkeypatch, tmp_path: Path) -> None:
    """Single-GPU branch fires model_load_start, processing_start,
    processing_end through progress_callback, in that order."""
    import torch
    from flake_analysis.core.pipeline import sam as sam_mod

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)

    fake_summary: dict = {}  # vendor returns mapping; sam.run_sam wraps it

    def fake_vendor_infer(*args, progress_callback=None, **kwargs):
        return fake_summary

    monkeypatch.setattr(sam_mod, "_vendor_infer", fake_vendor_infer)

    progress_calls: list[tuple[float, str]] = []
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    weights = tmp_path / "w.pt"
    weights.touch()

    sam_mod.run_sam(
        images_dir=images_dir,
        weights_path=weights,
        out_dir=out_dir,
        progress_callback=lambda p, m: progress_calls.append((p, m)),
    )

    assert _markers(progress_calls) == [
        "marker:model_load_start",
        "marker:processing_start",
        "marker:processing_end",
    ]


def test_run_sam_multi_gpu_emits_three_markers(monkeypatch, tmp_path: Path) -> None:
    """Multi-GPU branch fires the same three markers via progress_callback,
    in the same order, regardless of empty-input early-return."""
    import torch
    from flake_analysis.core.pipeline import sam as sam_mod

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 8)

    # Empty input dir — exercises the early-return code path.
    progress_calls: list[tuple[float, str]] = []
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    weights = tmp_path / "w.pt"
    weights.touch()

    sam_mod.run_sam(
        images_dir=images_dir,
        weights_path=weights,
        out_dir=out_dir,
        progress_callback=lambda p, m: progress_calls.append((p, m)),
    )

    assert _markers(progress_calls) == [
        "marker:model_load_start",
        "marker:processing_start",
        "marker:processing_end",
    ]
