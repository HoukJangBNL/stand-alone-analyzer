"""Shape test for the hand-written v7 migration module.

Asserts the alembic revision identifiers and presence of upgrade/downgrade.
This test is intentionally PG-free — it only loads the module from the file.

Note: ``alembic/versions/`` is not a Python package (no ``__init__.py``) and
the file name starts with a digit, so ``importlib.import_module`` cannot load
it. We use ``importlib.util.spec_from_file_location`` which is the canonical
way to load an arbitrary Python file by path.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0002_v7_auth.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "_v7_auth_migration_under_test", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"could not build spec for {_MIGRATION_PATH}"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_v7_migration_module_exists() -> None:
    assert _MIGRATION_PATH.exists(), f"missing migration file: {_MIGRATION_PATH}"
    mod = _load_migration_module()
    assert mod.revision == "0002_v7_auth"
    assert mod.down_revision == "0001_initial_v6"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)
