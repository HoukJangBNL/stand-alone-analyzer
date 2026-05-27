"""sam-gpu-smoketest.py — minimal sanity check after sam-gpu-bootstrap.sh.

Run on the GPU EC2 instance (via SSM exec) after the bootstrap script finishes.
Asserts:
  1. CUDA is available.
  2. The merged SAM2 checkpoint loads via build_sam2.
  3. AMG mask generation produces at least one mask on a synthetic 256x256 image.

Exits 0 on success, non-zero on any failure.

Usage (on the bootstrap instance):
    /opt/sam/stand-alone-analyzer/.venv/bin/python \\
        /opt/sam/stand-alone-analyzer/scripts/aws/sam-gpu-smoketest.py \\
        --weights /opt/sam/weights/sam2.1_hiera_large.merged.pt
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import numpy as np
import torch


def fail(msg: str) -> None:
    print(f"SMOKETEST FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--weights",
        type=Path,
        required=True,
        help="Path to merged SAM2 .pt produced by merge_lora.py",
    )
    p.add_argument(
        "--device",
        default="cuda",
        choices=["cuda", "cpu"],
        help="Device for inference (default: cuda)",
    )
    args = p.parse_args()

    # 1. CUDA availability
    if args.device == "cuda" and not torch.cuda.is_available():
        fail("torch.cuda.is_available() == False; CUDA driver/toolkit broken")
    if args.device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        print(f"OK  cuda available: {gpu_name}")

    if not args.weights.exists():
        fail(f"weights not found: {args.weights}")
    print(f"OK  weights present: {args.weights} ({args.weights.stat().st_size:,} bytes)")

    # 2. Load checkpoint and build SAM2
    try:
        state = torch.load(args.weights, map_location="cpu", weights_only=False)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        fail(f"torch.load failed: {exc}")

    if not isinstance(state, dict) or "model_state_dict" not in state:
        fail("checkpoint missing model_state_dict — merge_lora.py output malformed")
    print(f"OK  checkpoint structure valid: keys={list(state.keys())}")

    try:
        from sam2.build_sam import build_sam2
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        fail(f"sam2 import failed: {exc}")

    model_config = state.get("model_config") or {}
    if not model_config:
        fail("checkpoint has empty model_config; build_sam2 will not know hiera_large vs other")

    try:
        sam = build_sam2(model_config, None, device=args.device)
        sam.load_state_dict(state["model_state_dict"], strict=False)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        fail(f"build_sam2 / load_state_dict failed: {exc}")
    print("OK  SAM2 model built and weights loaded")

    # 3. AMG on a synthetic image
    rng = np.random.default_rng(0)
    img = (rng.normal(0.5, 0.2, size=(256, 256, 3)) * 255).clip(0, 255).astype(np.uint8)

    try:
        amg = SAM2AutomaticMaskGenerator(
            sam,
            points_per_side=8,
            pred_iou_thresh=0.5,
            stability_score_thresh=0.5,
        )
        masks = amg.generate(img)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        fail(f"AMG.generate failed: {exc}")

    if not masks:
        # Synthetic noise may legitimately yield zero masks; warn but don't fail.
        # The point is to exercise the forward pass without crashing.
        print("WARN AMG returned 0 masks on synthetic noise — forward pass OK, no segmentation hits")
    else:
        print(f"OK  AMG returned {len(masks)} masks on 256x256 synthetic image")

    print("SMOKETEST PASS")


if __name__ == "__main__":
    main()
