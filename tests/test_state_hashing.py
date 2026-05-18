from flake_analysis.state.hashing import params_hash, file_mtime, dir_mtime_max
from pathlib import Path
import tempfile


def test_params_hash_stable():
    h1 = params_hash({"seed": 0, "max_images": 100})
    h2 = params_hash({"max_images": 100, "seed": 0})  # different key order
    assert h1 == h2


def test_params_hash_changes_on_change():
    h1 = params_hash({"seed": 0})
    h2 = params_hash({"seed": 1})
    assert h1 != h2


def test_file_mtime_missing_returns_none():
    assert file_mtime("/tmp/__nonexistent_file__") is None


def test_dir_mtime_max():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.txt").write_text("hi")
        m = dir_mtime_max(tmp)
        assert m is not None
        assert m > 0
