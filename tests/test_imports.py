"""M0 smoke tests — verify package imports."""
from __future__ import annotations


def test_package_import():
    import flake_analysis

    assert flake_analysis.__version__ == "0.1.1"


def test_subpackages_importable():
    import flake_analysis.state  # noqa: F401
    import flake_analysis.pipeline  # noqa: F401
    import flake_analysis.ui  # noqa: F401
    import flake_analysis.cache  # noqa: F401
