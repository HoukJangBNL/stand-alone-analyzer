"""Project lifecycle schemas per backend design §1.1."""
from __future__ import annotations
from pydantic import BaseModel

class CreateProjectRequest(BaseModel):
    """POST /projects body. All fields optional — empty body creates the default 'local' project.

    When analysis_folder is omitted the route falls back to SAA_ANALYSIS_FOLDER
    (then to /mnt/analysis literal). raw_images_dir and annotations_path remain
    optional to match the existing manifest contract.
    """
    analysis_folder: str | None = None
    raw_images_dir: str | None = None
    annotations_path: str | None = None

class ProjectHandle(BaseModel):
    """Opaque project identifier + paths."""
    project_id: str
    analysis_folder: str
    raw_images_dir: str | None = None
    annotations_path: str | None = None

class ValidatePathsRequest(BaseModel):
    """POST /projects/validate-paths body."""
    analysis_folder: str | None = None
    raw_images_dir: str | None = None
    annotations_path: str | None = None

class PathStatus(BaseModel):
    """Per-path validation result."""
    exists: bool
    is_dir: bool
    is_file: bool
    readable: bool
    writable: bool
    canonical: str

class ValidatePathsResponse(BaseModel):
    """POST /projects/validate-paths response."""
    analysis_folder: PathStatus | None = None
    raw_images_dir: PathStatus | None = None
    annotations_path: PathStatus | None = None
