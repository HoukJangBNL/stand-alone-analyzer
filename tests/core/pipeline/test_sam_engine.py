"""Engine adapter test: run_sam dispatches to vendor.run_amg_v2_inference.infer
and translates progress events into ProgressCallback format."""
from pathlib import Path
from unittest.mock import patch

from flake_analysis.core.pipeline.sam import run_sam


def test_run_sam_calls_vendor_infer(tmp_path):
    images_dir = tmp_path / "imgs"
    images_dir.mkdir()
    out_dir = tmp_path / "out"

    progress_emits = []

    with patch("flake_analysis.core.pipeline.sam._vendor_infer") as vinfer:
        # Simulate vendor calling our progress shim with 2 events
        def fake_infer(*, images_dir, weights_path, out_dir, device, progress_callback):
            progress_callback({"idx": 1, "total": 2, "image_name": "a.png", "n_masks": 5, "error": None})
            progress_callback({"idx": 2, "total": 2, "image_name": "b.png", "n_masks": 0, "error": "IndexError: x"})
            return {
                "a.png": {"n_masks": 5, "error": None},
                "b.png": {"n_masks": 0, "error": "IndexError: x"},
            }
        vinfer.side_effect = fake_infer

        run_sam(
            images_dir=images_dir,
            weights_path=tmp_path / "merged.pt",
            out_dir=out_dir,
            device="cpu",
            progress_callback=lambda pct, msg: progress_emits.append((pct, msg)),
        )

    # Filter out marker:* progress events (timing harness, see Task 3 of
    # the GPU measurement harness plan); this test only cares about the
    # vendor-shim translation.
    shim_emits = [(p, m) for (p, m) in progress_emits if not m.startswith("marker:")]
    # 0.5 (1/2), 1.0 (2/2)
    assert len(shim_emits) == 2
    assert shim_emits[0][0] == 0.5
    assert "a.png" in shim_emits[0][1]
    assert shim_emits[1][0] == 1.0
    assert "b.png" in shim_emits[1][1]

    # Per-image results manifest is written
    assert (out_dir / "per_image_results.json").exists()
