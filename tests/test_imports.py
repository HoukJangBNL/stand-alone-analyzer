"""M0 smoke tests — verify package imports.

Plan 5 Task 17: drop the deleted `flake_analysis.ui` import; replace
the hard-coded version assertion with a shape check so future bumps
don't ripple here.
"""
from __future__ import annotations
import re


def test_package_import():
    import flake_analysis

    # Plan 5 ships v0.3.0 (Task 19). We only assert the shape (semver-ish
    # string with at least one dot) so future patches don't repeatedly
    # break this test.
    assert isinstance(flake_analysis.__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+", flake_analysis.__version__) is not None


def test_subpackages_importable():
    import flake_analysis.state  # noqa: F401
    import flake_analysis.pipeline  # noqa: F401
    # Plan 5 Task 14 deleted flake_analysis.ui; do NOT import it here.
    import flake_analysis.cache  # noqa: F401
    import flake_analysis.core  # noqa: F401
    import flake_analysis.api  # noqa: F401
