from pathlib import Path

import pytest

from flake_analysis.api.errors import ParamsInvalid
from flake_analysis.api.services.path_safety import safe_join


def test_safe_join_rejects_dot_dot_segment(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "..", "etc", "passwd")


def test_safe_join_rejects_embedded_dot_dot(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "foo/../../etc/passwd")


def test_safe_join_rejects_absolute_path(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "/etc/passwd")


def test_safe_join_rejects_backslash(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "a\\b")


def test_safe_join_rejects_non_allowlist_chars(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "ix003 iy017.webp")  # space disallowed


def test_safe_join_rejects_null_byte(tmp_path: Path):
    with pytest.raises(ParamsInvalid):
        safe_join(tmp_path, "ix003\x00.webp")


def test_safe_join_accepts_valid_filename(tmp_path: Path):
    out = safe_join(tmp_path, "lod0", "ix003_iy017.webp")
    assert out == tmp_path / "lod0" / "ix003_iy017.webp"


def test_safe_join_accepts_dotted_filename(tmp_path: Path):
    out = safe_join(tmp_path, "ix003_iy017.webp")
    assert out.name == "ix003_iy017.webp"


def test_safe_join_resolves_inside_base(tmp_path: Path):
    out = safe_join(tmp_path, "a", "b.webp")
    # resolved path must remain a child of tmp_path
    assert tmp_path in out.parents or out == tmp_path / "a" / "b.webp"
