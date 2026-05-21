"""Compatibility shim for the `msg` logger and the canonical
`ProgressCallback` type alias used across `flake_analysis.core`.

Originally this module also exposed `OperationContext` and `AnalysisTree`
shims for the Qpress reverse-merge contract; both were unused in the
standalone repo and were removed in W4.1 (see W0 audit
`claudedocs/pipeline-core-audit.md` §6).
"""
from __future__ import annotations

import logging
from typing import Callable

# --- msg shim ----------------------------------------------------------------
# Replaces `from infrastructure.shared.core.message_handler import msg`.
# Standalone uses Python stdlib logging.

_logger = logging.getLogger("flake_analysis.core")


# --- ProgressCallback type alias --------------------------------------------
# Single canonical signature for progress reporting across the pipeline:
#   cb(pct, message)
# - pct: float in [0.0, 1.0] (monotonic non-decreasing per call site)
# - message: short human-readable status string
#
# Always optional with a default of ``None`` in the pipeline wrappers, so
# existing call sites that don't care about progress remain backward
# compatible. New in v0.2.0.
ProgressCallback = Callable[[float, str], None]


class _Msg:
    """Minimal subset of Qpress's msg API. Only the methods extracted code uses."""

    def info(self, m: str) -> None:
        _logger.info(m)

    def debug(self, m: str) -> None:
        _logger.debug(m)

    def warning(self, m: str) -> None:
        _logger.warning(m)

    def error(self, m: str) -> None:
        _logger.error(m)


msg = _Msg()
