"""Error envelope shape per integrated design §6."""
from __future__ import annotations
import uuid
from typing import Any
from pydantic import BaseModel
from fastapi import Request, status
from fastapi.responses import JSONResponse


class ErrorDetail(BaseModel):
    """Error envelope shape."""
    code: str
    message: str
    details: dict[str, Any] = {}
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class AppError(Exception):
    """Base for all application errors. Subclasses define code + HTTP status."""
    code: str = "internal_error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "An internal error occurred"

    def __init__(self, **details: Any):
        self.details = details
        super().__init__(self.message)

    def to_response(self) -> dict:
        """Build error envelope dict."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "request_id": str(uuid.uuid4()),
            }
        }


class ParamsInvalid(AppError):
    code = "params_invalid"
    status_code = status.HTTP_400_BAD_REQUEST
    message = "Invalid request parameters"


class PrerequisiteMissing(AppError):
    code = "prerequisite_missing"
    status_code = status.HTTP_409_CONFLICT
    message = "Prerequisite step not completed"


class ArtifactMissing(AppError):
    code = "artifact_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Required artifact file not found"


class ProjectBusy(AppError):
    code = "project_busy"
    status_code = status.HTTP_423_LOCKED
    message = "Project is currently locked by another operation"


class DomainStatsNotFound(AppError):
    code = "domain_stats_not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Domain Stats not computed yet. Run Compute → Domain Stats first."


class SelectionNotFound(AppError):
    code = "selection_not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "No selection committed yet. Click Commit on the Selector tab."


class AnnotationsPathUnset(AppError):
    code = "annotations_path_unset"
    status_code = status.HTTP_400_BAD_REQUEST
    message = "annotations_path is not configured for this project."


class DomainNotFound(AppError):
    code = "domain_not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Domain not found in annotations."


class ClusteringNotFitted(AppError):
    code = "clustering_not_fitted"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Clustering has not been fitted yet. Click Fit GMM on the Clustering tab."


class SeedGroupsMissing(AppError):
    code = "seed_groups_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "No seed groups committed yet."


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """FastAPI exception handler for AppError subclasses."""
    envelope = exc.to_response()
    try:
        from flake_analysis.api.logging_ctx import get_request_id
        rid = get_request_id()
        if rid:
            envelope["error"]["request_id"] = rid
    except ImportError:
        pass
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope,
    )
