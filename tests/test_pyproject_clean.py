"""Plan 5 Task 25 — assert post-cutover `pyproject.toml` is clean.

Specifically:
- `streamlit` does not appear in [project].dependencies or anywhere
  else in the file.
- `plotly` does not appear anywhere in the file.
- `version` is on or after 0.3.0.
- `description` no longer claims the package is a Streamlit app.
"""
from __future__ import annotations
import re
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_pyproject_exists():
    assert PYPROJECT.exists(), f"missing {PYPROJECT}"


def test_streamlit_not_in_runtime_deps():
    deps = _load()["project"]["dependencies"]
    for dep in deps:
        assert "streamlit" not in dep.lower(), (
            f"Plan 5 Task 18 FAILED — streamlit re-appeared in dependencies: {dep!r}"
        )


def test_plotly_not_in_runtime_deps():
    deps = _load()["project"]["dependencies"]
    for dep in deps:
        assert "plotly" not in dep.lower(), (
            f"Plan 5 Task 18 FAILED — plotly re-appeared in dependencies: {dep!r}"
        )


def test_streamlit_not_anywhere_in_file():
    text = PYPROJECT.read_text(encoding="utf-8")
    # Allow the literal in a hypothetical comment if a future maintainer
    # writes something like `# Note: Streamlit removed in Plan 5`. Fail
    # on any real declaration.
    declarations = re.findall(r'"streamlit[^"]*"', text)
    assert declarations == [], (
        f"streamlit still declared somewhere in pyproject.toml: {declarations}"
    )


def test_plotly_not_anywhere_in_file():
    text = PYPROJECT.read_text(encoding="utf-8")
    declarations = re.findall(r'"plotly[^"]*"', text)
    assert declarations == [], (
        f"plotly still declared somewhere in pyproject.toml: {declarations}"
    )


def test_version_is_at_least_0_3_0():
    version = _load()["project"]["version"]
    parts = version.split(".")
    assert len(parts) >= 3, f"unexpected version shape: {version!r}"
    major, minor, *_ = (int(p) for p in parts[:2] + [0])
    # 0.3.0+ OR 1.x+
    assert (major == 0 and minor >= 3) or major >= 1, (
        f"version {version!r} is older than 0.3.0 — Plan 5 Task 19 not yet applied?"
    )


def test_description_is_not_streamlit():
    description = _load()["project"]["description"]
    assert "Streamlit" not in description, (
        f"description still claims Streamlit: {description!r}"
    )
    assert "streamlit" not in description.lower(), (
        f"description still mentions streamlit: {description!r}"
    )
