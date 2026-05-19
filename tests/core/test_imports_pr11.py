"""M1 PR 1.1 import smoke tests.

Verify the public API of the newly extracted submodules is reachable and
no module imports forbidden Qpress-internal paths.
"""
from __future__ import annotations

import ast
from pathlib import Path


def test_annotations_import():
    from flake_analysis.core.annotations import (  # noqa: F401
        AnnotationsCache,
        FlakeMetadata,
        RLEFlake,
        load_flakes_from_annotations,
    )


def test_image_processing_import():
    from flake_analysis.core.image_processing import (  # noqa: F401
        get_median_background,
        process_image,
        union_find_islands,
    )


def test_pipeline_import():
    from flake_analysis.core.pipeline import (  # noqa: F401
        run_background,
        run_domain_proximity,
    )


def test_no_qpress_imports():
    """No module under flake_analysis.core/ may import infrastructure.* / modules.* / utils.*"""
    src_root = Path(__file__).parent.parent.parent / "src" / "flake_analysis" / "core"
    forbidden = ("infrastructure", "modules", "utils")
    offenders: list[str] = []

    for py in src_root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            offenders.append(f"{py}: parse error {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name or ""
                    for f in forbidden:
                        if name == f or name.startswith(f + "."):
                            offenders.append(f"{py.relative_to(src_root)}: import {name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for f in forbidden:
                    if module == f or module.startswith(f + "."):
                        offenders.append(
                            f"{py.relative_to(src_root)}: from {module} import ..."
                        )

    assert not offenders, "Forbidden imports detected:\n  " + "\n  ".join(offenders)
