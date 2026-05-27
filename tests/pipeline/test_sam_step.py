"""SAM step wrapper test — resolves out_dir from analysis_folder + SUBDIRS, delegates to core engine."""
from pathlib import Path
from unittest.mock import patch

from flake_analysis.pipeline.sam import run_sam_step


def test_run_sam_step_dispatches_to_engine(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    weights = tmp_path / "merged.pt"
    weights.write_bytes(b"")

    with patch("flake_analysis.pipeline.sam.run_sam") as eng:
        eng.return_value = {"images": 0, "masks_total": 0, "errors": 0, "per_image": {}}
        run_sam_step(
            raw_images_dir=raw,
            analysis_folder=analysis,
            weights_path=weights,
            device="cpu",
            progress_callback=None,
        )

    eng.assert_called_once()
    kwargs = eng.call_args.kwargs
    assert kwargs["images_dir"] == raw
    assert kwargs["out_dir"] == analysis / "07_sam"
    assert kwargs["weights_path"] == weights
    assert kwargs["device"] == "cpu"
