# tests/test_deploy_script.py
"""Deploy-script shape test (Plan 5 Task 6).

The script's behavior on a real host is not unit-testable (it touches
/usr/share, runs systemctl, etc.). This test asserts the script:
- exists and is executable;
- is a bash script (`#!/usr/bin/env bash`);
- uses `set -euo pipefail` so partial failures abort;
- mentions the canonical paths from deployment-design.md §2 and §5;
- performs the symlink rotation atomically (uses `ln -sfn` or
  `mv -T`).
"""
from __future__ import annotations
import os
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "deploy" / "scripts" / "deploy.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT_PATH.exists(), f"missing deploy script at {SCRIPT_PATH}"
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, "deploy.sh must be user-executable (chmod +x)"


def test_script_starts_with_bash_shebang():
    first_line = SCRIPT_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!/"), "missing shebang"
    assert "bash" in first_line, "deploy.sh must be a bash script"


def test_script_uses_strict_mode():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text, (
        "deploy.sh must `set -euo pipefail` so partial failures abort"
    )


def test_script_references_canonical_paths():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    # web bundle goes under /usr/share/stand-alone-analyzer/web (deployment-design §2)
    assert "/usr/share/stand-alone-analyzer" in text
    # systemd unit name is saa-api per Task 5
    assert "saa-api" in text
    # nginx site name matches the conf filename from Task 4
    assert "stand-alone-analyzer" in text


def test_script_uses_atomic_symlink_swap():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    # `ln -sfn` (symbolic, force, no-deref) is the canonical atomic
    # symlink-replace idiom on Linux. `mv -T` is acceptable too.
    assert ("ln -sfn" in text) or ("mv -T" in text), (
        "deploy.sh must rotate the release symlink atomically (ln -sfn or mv -T)"
    )


def test_script_runs_systemctl_reload_or_restart():
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "systemctl" in text
    assert ("restart saa-api" in text) or ("reload saa-api" in text), (
        "deploy.sh must restart or reload the saa-api unit after deploying"
    )
    assert ("nginx -s reload" in text) or ("systemctl reload nginx" in text), (
        "deploy.sh must reload nginx after publishing the new web bundle"
    )
