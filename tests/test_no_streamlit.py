"""Plan 5 Task 24 (pinned decision #12) — guard against any future
Streamlit re-introduction.

Greps the source tree at test-collection time. The test fails if ANY
Streamlit token leaks back into src/, tests/, or app/. Excludes:
- this file (which contains the literal `streamlit` strings as test data),
- /tmp / cache / venv directories (not part of the package).
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PATTERN = r"^import streamlit|^from streamlit|streamlit\."
SCAN_DIRS = ["src", "tests", "app"]


def _grep(pattern: str, paths: list[str]) -> list[str]:
    """Return matching lines as `path:lineno:line`. Empty list if no match."""
    real_paths = [str(REPO_ROOT / p) for p in paths if (REPO_ROOT / p).exists()]
    if not real_paths:
        return []
    result = subprocess.run(
        ["grep", "-rEn", "--include=*.py", pattern, *real_paths],
        capture_output=True,
        text=True,
    )
    # grep returns 1 when nothing matched — that's our happy path.
    if result.returncode not in (0, 1):
        raise RuntimeError(f"grep failed: rc={result.returncode} stderr={result.stderr!r}")
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def test_no_streamlit_imports_in_source_tree():
    """No `import streamlit`, `from streamlit`, or `streamlit.` token outside this file."""
    matches = _grep(PATTERN, SCAN_DIRS)
    self_path = str(Path(__file__).resolve())
    foreign = [m for m in matches if not m.startswith(self_path)]
    assert foreign == [], (
        "Plan 5 cutover guard FAILED — Streamlit reference re-appeared:\n"
        + "\n".join(foreign)
    )


def test_streamlit_module_is_not_importable_from_package():
    """`flake_analysis.ui` must not exist anywhere in the package tree."""
    ui_dir = REPO_ROOT / "src" / "flake_analysis" / "ui"
    assert not ui_dir.exists(), (
        f"Plan 5 cutover guard FAILED — {ui_dir} was re-introduced. "
        "The Streamlit UI was deleted by Plan 5 Task 14; do not restore it."
    )


def test_streamlit_app_entrypoint_is_gone():
    """`app/streamlit_app.py` must not exist."""
    entry = REPO_ROOT / "app" / "streamlit_app.py"
    assert not entry.exists(), (
        f"Plan 5 cutover guard FAILED — {entry} was re-introduced. "
        "The Streamlit entrypoint was deleted by Plan 5 Task 12."
    )


def test_grep_pattern_self_test():
    """Sanity check: the regex matches a known-positive line.

    Without this, a future refactor that quietly broke the regex would
    make the guard above silently pass.
    """
    positive = "import streamlit as st"
    assert re.search(PATTERN, positive) is not None, "guard regex broke"
    negative = "import pandas as pd"
    assert re.search(PATTERN, negative) is None, "guard regex over-matches"
