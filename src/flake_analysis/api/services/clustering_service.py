"""Clustering data readers — labels.json + assignments.parquet + seed_groups.json."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa


def load_labels_json(analysis_folder: str | Path) -> dict[str, Any]:
    """Read 04_clustering/labels.json. Raises FileNotFoundError if missing."""
    p = Path(analysis_folder) / "04_clustering" / "labels.json"
    if not p.exists():
        raise FileNotFoundError(f"labels.json missing at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def load_assignments_table(analysis_folder: str | Path) -> pa.Table:
    """Read 04_clustering/assignments.parquet as an Arrow table."""
    p = Path(analysis_folder) / "04_clustering" / "assignments.parquet"
    if not p.exists():
        raise FileNotFoundError(f"assignments.parquet missing at {p}")
    df = pd.read_parquet(p)
    return pa.Table.from_pandas(df, preserve_index=False)


def load_seed_groups(analysis_folder: str | Path) -> list[dict[str, Any]]:
    """Read 04_clustering/seed_groups.json. Missing file returns []."""
    p = Path(analysis_folder) / "04_clustering" / "seed_groups.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))
