"""Compatibility shims for Qpress dependencies removed in standalone extraction.

These shims let extracted Qpress code (modules/analyzer/*, utils/image_processing/*)
run without the full Qpress runtime (msg, OperationContext, AnalysisTree).

When this package is imported by Qpress itself, Qpress can monkey-patch these
back to its native implementations. When imported by the standalone Streamlit
app, the shims are used directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

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


# --- OperationContext stub ---------------------------------------------------
# Replaces Qpress's OperationContext for files that consume `ctx.params` etc.
# Standalone passes plain dicts; this stub preserves attribute access shape.


@dataclass
class OperationContext:
    """Plain dataclass shim of Qpress's OperationContext.

    Extracted operations only need params + state-style fields.
    Standalone code may build one of these in pipeline wrappers if needed.
    """

    params: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    workspace: Optional[str] = None
    progress_callback: Optional[Callable[[float, str], None]] = None

    async def report_progress(self, percentage: float, message: str = "") -> None:
        if self.progress_callback is not None:
            self.progress_callback(percentage, message)

    async def check_cancellation(self) -> None:
        # Standalone has no cancellation infrastructure (single-process Streamlit).
        return None


# --- AnalysisTree no-op ------------------------------------------------------
# Replaces Qpress's AnalysisTree dependency tracking.
# Standalone uses manifest.json instead — see stand-alone-analyzer state/manifest.py.


class AnalysisTree:
    """No-op shim — standalone uses manifest.json for dependency tracking."""

    @staticmethod
    def write_meta_json(*args: Any, **kwargs: Any) -> None:
        return None

    @staticmethod
    def register(*args: Any, **kwargs: Any) -> None:
        return None

    @staticmethod
    def get_parent(*args: Any, **kwargs: Any) -> None:
        return None
