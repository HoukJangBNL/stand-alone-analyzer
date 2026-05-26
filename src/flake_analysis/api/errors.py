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


class Forbidden(AppError):
    code = "forbidden"
    status_code = status.HTTP_403_FORBIDDEN
    message = "Forbidden"


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


class ExplorerStateMissing(AppError):
    code = "explorer_state_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "No explorer state saved yet. Click Save on the Explorer tab."


class ThumbnailMissing(AppError):
    code = "thumbnail_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Thumbnail not found for the requested LOD/stem."


class RawImageMissing(AppError):
    code = "raw_image_missing"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Raw substrate image not found."


class FlakeNotFound(AppError):
    code = "flake_not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Flake not found for the requested flake_id."


class DbUnavailable(AppError):
    code = "db_unavailable"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "Database temporarily unavailable"


class DuplicateProjectName(AppError):
    code = "duplicate_project_name"
    status_code = status.HTTP_409_CONFLICT
    message = "Project with this name already exists"


class ProjectNotFound(AppError):
    code = "project_not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Project not found"


class ProjectHasScans(AppError):
    code = "project_has_scans"
    status_code = status.HTTP_409_CONFLICT
    message = "Project still has scans; delete or move them first"


# ── Upload-path errors (B6) ────────────────────────────────────────────────
# Codes mirror the `event=` names emitted by A4 logging sites in routes/scans.py
# so observability and frontend error display use the same vocabulary.

class S3NotConfigured(AppError):
    code = "s3_not_configured"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "SAA_S3_BUCKET not configured"


class ScanNotFound(AppError):
    code = "scan_not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Scan not found"


class PresignCollisionSha256(AppError):
    code = "presign_collision_sha256"
    status_code = status.HTTP_409_CONFLICT
    message = "sha256 already uploaded for this scan"


class PresignCollisionGrid(AppError):
    code = "presign_collision_grid"
    status_code = status.HTTP_409_CONFLICT
    message = "grid coordinates already used for this scan"


class PresignIdempotentBucketMismatch(AppError):
    code = "presign_idempotent_bucket_mismatch"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "upload_item s3_uri references unexpected bucket"


class PresignUploadItemConflict(AppError):
    code = "presign_upload_item_conflict"
    status_code = status.HTTP_409_CONFLICT
    message = "upload_item insert conflict"


class UploadItemNotFound(AppError):
    code = "upload_item_not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "upload_item not found"


class UploadItemScanMismatch(AppError):
    code = "upload_item_scan_mismatch"
    status_code = status.HTTP_404_NOT_FOUND
    message = "upload_item does not belong to the requested scan"


class CompleteInvalidS3Uri(AppError):
    code = "complete_invalid_s3_uri"
    status_code = status.HTTP_409_CONFLICT
    message = "upload_item has invalid s3_uri"


class CompleteS3ObjectMissing(AppError):
    code = "complete_s3_object_missing"
    status_code = status.HTTP_409_CONFLICT
    message = "S3 object not found - upload did not complete"


class CompleteS3HeadError(AppError):
    code = "complete_s3_head_error"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "S3 head_object failed"


class CompleteImageConflict(AppError):
    code = "complete_image_conflict"
    status_code = status.HTTP_409_CONFLICT
    message = "image insert conflict"


class FinalizeIncomplete(AppError):
    code = "finalize_incomplete"
    status_code = status.HTTP_409_CONFLICT
    message = "Scan has missing uploads; finalize blocked"


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
