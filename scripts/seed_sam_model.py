"""Seed a SAM model row into the models table.

Idempotent: checks if a Model row exists before inserting. Safe to re-run.

The Model row represents the fine-tuned SAM2.1+LoRA checkpoint used for
graphene flake detection. This satisfies the Phase 4 P4.1 prerequisite
that `get_or_create_default_analysis` requires at least one Model row.

IMPORTANT: The s3_uri points to the merged.pt checkpoint (LoRA folded into
base weights), but the actual 8-GPU inference path (_run_sam_multi_gpu)
resolves weights from the AMI's M3 bundle (T7l disables merged_m3, forcing
LoRA-runtime from /opt/sam/m3). This Model row primarily:
  - Satisfies the P4.1 prerequisite (DB constraint)
  - Binds to Analysis.model_id (FK)
  - Records model metadata for runs table

It does NOT redirect which weights the GPU worker loads at runtime (that's
determined by SAM_MERGED_M3_PATH env + M3 bundle presence, per core/pipeline/sam.py).
"""
from __future__ import annotations
import asyncio
import sys

from sqlalchemy import select

from flake_analysis.db import async_session_maker
from flake_analysis.db.models import Model


async def seed_model() -> int:
    """Insert Model row if none exists. Returns 0 on success, 1 on error."""
    async with async_session_maker() as session:
        # Check if any Model exists
        existing = (await session.execute(select(Model))).scalar_one_or_none()
        if existing is not None:
            print(f"Model already exists: id={existing.id}, name={existing.name}")
            return 0

        # Insert the fine-tuned SAM model
        model = Model(
            name="sam2.1_hiera_large.merged.c7ed20f8",
            base_model="sam2.1_hiera_large",
            s3_uri="s3://qpress-uploads/internal/sam/sam2.1_hiera_large.merged.c7ed20f8.pt",
            description=(
                "Fine-tuned SAM2.1 Hiera-L with LoRA merged into base weights. "
                "Used for graphene flake segmentation. "
                "Note: 8-GPU worker path uses LoRA-runtime from M3 bundle; "
                "this s3_uri is for metadata/single-GPU path only."
            ),
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)

        print(f"✅ Model seeded: id={model.id}, name={model.name}")
        print(f"   base_model: {model.base_model}")
        print(f"   s3_uri: {model.s3_uri}")
        print(f"   created_at: {model.created_at}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(seed_model()))
