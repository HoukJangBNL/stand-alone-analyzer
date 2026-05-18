"""manifest.json read/write + stale detection.

Per plan v1 r7 §7 schema.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional, List

from flake_analysis.state.paths import PIPELINE_STEPS, manifest_path

MANIFEST_VERSION = 1


@dataclass
class StepEntry:
    """Per-step manifest record. Matches plan §7."""
    completed_at: Optional[str] = None  # ISO 8601
    params: Dict[str, Any] = field(default_factory=dict)
    params_hash: Optional[str] = None
    input_hashes: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)  # output_name -> relative path
    reproducibility: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    created_at: Optional[str] = None
    raw_images_dir: Optional[str] = None
    annotations_path: Optional[str] = None
    analysis_folder: Optional[str] = None
    flake_core_version: Optional[str] = None
    steps: Dict[str, StepEntry] = field(default_factory=dict)


def load_manifest(analysis_folder: str | Path) -> Manifest:
    """Load manifest.json, or return a fresh Manifest if file does not exist."""
    p = manifest_path(analysis_folder)
    if not p.exists():
        return Manifest()
    raw = json.loads(p.read_text(encoding="utf-8"))
    steps = {
        step_name: StepEntry(**step_data)
        for step_name, step_data in raw.get("steps", {}).items()
    }
    raw.pop("steps", None)
    return Manifest(steps=steps, **raw)


def save_manifest(manifest: Manifest, analysis_folder: str | Path) -> None:
    """Atomic write of manifest.json."""
    p = manifest_path(analysis_folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(manifest)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)


def step_status(manifest: Manifest, step: str) -> str:
    """Return one of: 'not_started', 'done', 'stale'.

    A step is 'stale' if its params_hash or any upstream input_hash differs
    from the recorded value (warn-only; never auto-deletes).
    """
    if step not in PIPELINE_STEPS:
        raise ValueError(f"unknown step: {step}")
    entry = manifest.steps.get(step)
    if entry is None or entry.completed_at is None:
        return "not_started"
    # TODO: stale detection — compare params_hash to current UI params,
    # compare upstream input_hashes to current upstream params_hash.
    # For PR 2.1, just return 'done' if completed_at is set.
    return "done"
