# Pipeline Params Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename and prune pipeline parameters across backend + frontend so the UI presents intuitive, non-redundant fields, and move μm calibration out of the pipeline params blob to project-level metadata.

**Architecture:** Five-layer change — core pipeline kwargs (selective rename, breaking changes deferred to API layer to limit blast radius), Pydantic API schemas with Field aliases for one-cycle backcompat, alembic data migration that rewrites stored `pipeline_params` JSONB blobs and adds `projects.pixel_size_um`, a new `GET /sam/models` endpoint enumerating S3 models, and a frontend `PipelineParamsForm` rewrite with model dropdown.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, Pydantic v2, React+Vite+TS, S3 (boto3 list_objects_v2)

---

## Background — the 8 owner cleanup items

1. **`raw_ext` exposed in UI (Thumbnails + Domain Stats) but redundant.** The manifest already records the raw image extension at upload time. Surfacing it on the pipeline form forces the operator to keep it in sync manually and creates a divergence risk if the manifest disagrees with the form value. Fix: drop `raw_ext` from request schemas and the UI; the route handler resolves it from the active manifest before invoking the core pipeline (which keeps its kwarg unchanged).

2. **`force_recompute` is unintuitive.** The label reads as "force the run" rather than "ignore the cache". Rename to `regenerate_cache` everywhere (schema, UI label, persisted JSONB key). Behavior unchanged.

3. **`weights_path` is a free-text absolute path.** Operators have to remember the bastion-side filesystem location of `*.pt` checkpoints. Fix: replace with a `model` selection backed by a new endpoint `GET /sam/models` that enumerates `s3://qpress-uploads/internal/sam/*.pt`. Endpoint returns `[{name, s3_uri}]` (stem of object key as display name). Frontend fetches this on PipelineParamsForm mount and renders a dropdown.

4. **`device` field with cuda/cpu/auto choice.** Operators do not need to know whether a GPU is attached — the worker should detect. Remove from UI/schema; the SAM core function continues to accept `device` but the API layer always passes `"auto"` (or omits it).

5. **`repr_mode` on Domain Stats.** Owner has settled on median as the only supported representative. Remove from UI/schema; pipeline-layer wrapper hardcodes `"median"`.

6. **`raw_ext` on Domain Stats.** Same as #1 — sourced from manifest.

7. **`pixel_size_um` + `link_distance_um` on Domain Proximity.** The pipeline core operates entirely in pixel domain. μm is a project-level calibration, not a per-run parameter — every run on the same scan should use the same μm/px. Fix: `pixel_size_um` moves to a new `projects.pixel_size_um` column. `link_distance_um` is removed from the request schema; a new `link_distance_px` (renamed `cluster_link_distance_px` per #8) becomes the only request field. μm display in result views computes `value_px * project.pixel_size_um` at render time.

8. **Field rename pass.** Old → new mapping (applied at API + UI; core kwargs left in place where rename is non-trivial — see AD2):

| Stage | Old | New |
|---|---|---|
| Thumbnails | `raw_ext` | (removed, auto from manifest) |
| Thumbnails | `quality` | `thumbnail_quality` |
| Thumbnails | `force_recompute` | `regenerate_cache` |
| Background | `seed` | `random_seed` |
| Background | `max_images` | `sample_size` |
| Background | `gaussian_sigma` | `blur_strength` (px) |
| Background | `method` | `aggregation` (median/mean) |
| SAM | `weights_path` | `model` (dropdown) |
| SAM | `device` | (removed, auto) |
| Domain Stats | `repr_mode` | (removed, median fixed) |
| Domain Stats | `raw_ext` | (removed, auto) |
| Domain Proximity | `r_max_px` | `neighbor_search_radius_px` |
| Domain Proximity | `min_area_px` | `min_flake_area_px` |
| Domain Proximity | `max_area_px` | `max_flake_area_px` |
| Domain Proximity | `d_touch_px` | `touch_threshold_px` |
| Domain Proximity | `link_distance_um` | (removed) |
| Domain Proximity | `pixel_size_um` | (removed; → projects.pixel_size_um column) |
| Domain Proximity | `link_distance_px` (new) | `cluster_link_distance_px` |
| Domain Proximity | `workers` | `parallel_workers` |

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/flake_analysis/api/schemas/compute.py` | Pydantic request/response schemas for run pipeline. Field renames + aliases land here. |
| `src/flake_analysis/api/routes/run.py` | Per-stage POST endpoints (`/run/thumbnails`, `/run/sam`, etc.). Resolves `raw_ext` from manifest; passes new schema field names through to wrapper. |
| `src/flake_analysis/api/routes/run_pipeline.py` | Composite "run all stages" endpoint. Same schema/manifest resolution. |
| `src/flake_analysis/api/routes/__init__.py` | Router registration — add new `/sam/models` router. |
| `src/flake_analysis/api/services/sam_models_service.py` | **NEW.** boto3 `list_objects_v2` against `s3://qpress-uploads/internal/sam/`, returns `[{name, s3_uri}]`. |
| `src/flake_analysis/core/pipeline/thumbnails.py` | Core thumbnail generation. Kwargs unchanged (see AD2). |
| `src/flake_analysis/core/pipeline/background.py` | Core background subtraction. Kwargs unchanged. |
| `src/flake_analysis/core/pipeline/sam.py` | Core SAM segmentation. Accepts `weights_path` (now resolved from S3 URI by API layer). |
| `src/flake_analysis/core/pipeline/domain_stats.py` | Core domain stats. Wrapper layer pins `repr_mode="median"`. |
| `src/flake_analysis/core/pipeline/domain_proximity.py` | Core proximity. Kwargs use existing pixel-domain names; μm conversion no longer happens here. |
| `src/flake_analysis/pipeline/thumbnails.py` | Wrapper used by API routes. Maps API field names → core kwargs. |
| `src/flake_analysis/pipeline/domain_stats.py` | Wrapper. Hardcodes `repr_mode="median"`; pulls `raw_ext` from manifest argument. |
| `src/flake_analysis/pipeline/domain_proximity.py` | Wrapper. Strips μm-domain fields, passes pixel-domain only. |
| `src/flake_analysis/pipeline/sam.py` | Wrapper. Resolves `model` (S3 URI) → local cached path before calling core. |
| `src/flake_analysis/db/models/projects.py` (or equivalent) | Add `pixel_size_um: Mapped[Decimal \| None]` column. |
| `alembic/versions/0007_pipeline_params_refactor.py` | **NEW.** Adds `projects.pixel_size_um`; data migration over `analyses.pipeline_params` JSONB. |
| `web/src/components/run/PipelineParamsForm.tsx` | Field rename + dropdown integration + pixel_size_um removal. |
| `web/src/components/run/__tests__/PipelineParamsForm.test.tsx` | Vitest coverage for new fields, dropdown loading + error states. |
| `web/src/api/sam.ts` (or co-located) | **NEW.** Client function `fetchSamModels()` calling `GET /sam/models`. |

---

## Open Decisions

### AD1 — `pixel_size_um` storage location

**Recommendation:** Add `projects.pixel_size_um numeric(10,4) NULL` column. Single calibration per project, set at project creation or first scan ingestion. UI surfaces it in project settings, not pipeline form.

**Alternatives considered:**
- **Scan-level** (`scans.pixel_size_um`): higher fidelity if a project mixes scopes, but adds UI complexity (operator must set per scan) and breaks the "one project = one scope" assumption.
- **Material-level** (`materials.pixel_size_um`): material-defined optical setup is rare in current data; would over-couple material catalog to acquisition.

**Owner sub-decision required:** confirm project-level. If owner picks scan-level, swap the alembic migration target and the read site in result views.

### AD2 — Core layer rename scope

**Recommendation:** Leave core pipeline kwargs (`r_max_px`, `min_area_px`, `gaussian_sigma`, etc.) untouched. Rename only at the API schema boundary; the wrapper in `src/flake_analysis/pipeline/*.py` translates new schema field names back to existing core kwargs. Rationale: core kwargs are referenced by parity harnesses and saved analysis JSONB blobs across many historical runs; renaming the core would force a wider data migration than the API-layer rename does.

**Alternatives considered:**
- **Full rename through to core**: cleaner long-term, but blast radius extends to algo-engineer's parity tests and any direct script callers. Defer to a future plan.

**Owner sub-decision required:** approve "rename at API boundary only". If owner requests full rename, add Phase 1.5 to update core + parity tests.

### AD3 — Backcompat strategy

**Recommendation:** Pydantic v2 `Field(..., alias="old_name", validation_alias=AliasChoices("old_name", "new_name"))` for one release cycle so already-saved analyses (with old field names in `pipeline_params`) still deserialize. Combined with the alembic data migration in Phase 2 that rewrites stored blobs, both paths are covered. Phase 5 removes the aliases.

### AD4 — SAM model S3 layout

**Recommendation:** `s3://qpress-uploads/internal/sam/*.pt` (flat). Display name = key stem (e.g., `sam_vit_h_4b8939.pt` → `sam_vit_h_4b8939`). Endpoint contract:

```
GET /sam/models
200 { "models": [ { "name": "sam_vit_h_4b8939", "s3_uri": "s3://qpress-uploads/internal/sam/sam_vit_h_4b8939.pt" } ] }
500 { "detail": "Failed to list SAM models" }  # S3 unreachable
```

Frontend handles 500 by disabling the dropdown and rendering an error pill ("Could not load models — retry").

---

## Phase 0: Audit (researcher)

**Output:** `docs/superpowers/plans/2026-05-27-pipeline-params-refactor-audit.md` listing every call site for the renamed/removed fields. This anchors Phase 1's edit list and prevents missed references.

### Task 0.1 — Enumerate call sites

**Files to read (no edits):**
- `src/flake_analysis/api/schemas/compute.py`
- `src/flake_analysis/api/routes/run.py`, `run_pipeline.py`
- `src/flake_analysis/pipeline/*.py`
- `src/flake_analysis/core/pipeline/*.py`
- `web/src/components/run/PipelineParamsForm.tsx`
- `web/src/components/run/__tests__/PipelineParamsForm.test.tsx`
- All test files under `tests/` referencing renamed fields

**Steps:**
- [ ] Run `grep -rn "raw_ext\|force_recompute\|weights_path\|repr_mode\|gaussian_sigma\|r_max_px\|min_area_px\|max_area_px\|d_touch_px\|link_distance_um\|link_distance_px\|pixel_size_um\|max_images\|gaussian_sigma\|workers" src/ web/src/ tests/`
- [ ] For each hit, classify as: schema | route | wrapper | core | UI | test | docs
- [ ] Write audit doc with sections per stage; flag any call site not covered by Phases 1–4
- [ ] Commit:
```
git add docs/superpowers/plans/2026-05-27-pipeline-params-refactor-audit.md
git commit -m "docs(plan): audit call sites for pipeline params refactor"
```

---

## Phase 1: Core+API rename + Field aliases (api-developer)

### Task 1.1 — Failing test for new ThumbnailsParams field names

**Files:**
- `tests/api/schemas/test_compute_schemas.py` (create if absent)

**Steps:**
- [ ] Write the failing test:
```python
# tests/api/schemas/test_compute_schemas.py
from flake_analysis.api.schemas.compute import ThumbnailsParams

def test_thumbnails_accepts_new_field_names():
    p = ThumbnailsParams(thumbnail_quality=85, regenerate_cache=True)
    assert p.thumbnail_quality == 85
    assert p.regenerate_cache is True

def test_thumbnails_accepts_legacy_aliases():
    p = ThumbnailsParams(quality=85, force_recompute=True)
    assert p.thumbnail_quality == 85
    assert p.regenerate_cache is True

def test_thumbnails_rejects_raw_ext():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ThumbnailsParams(thumbnail_quality=85, raw_ext=".png")
```
- [ ] Run test — confirm failure (delegate via api-developer agent, not direct PM bash)

### Task 1.2 — Update ThumbnailsParams schema

**Files:**
- `src/flake_analysis/api/schemas/compute.py`

**Steps:**
- [ ] Replace ThumbnailsParams body with:
```python
from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import AliasChoices

class ThumbnailsParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    thumbnail_quality: int = Field(85, validation_alias=AliasChoices("thumbnail_quality", "quality"))
    regenerate_cache: bool = Field(False, validation_alias=AliasChoices("regenerate_cache", "force_recompute"))
    # raw_ext removed — resolved from manifest in route handler
```
- [ ] Run tests — green
- [ ] Commit:
```
git add src/flake_analysis/api/schemas/compute.py tests/api/schemas/test_compute_schemas.py
git commit -m "feat(api): rename ThumbnailsParams fields with legacy aliases"
```

### Task 1.3 — BackgroundParams rename

**Files:**
- `src/flake_analysis/api/schemas/compute.py`
- `tests/api/schemas/test_compute_schemas.py`

**Steps:**
- [ ] Add failing test:
```python
def test_background_accepts_new_field_names():
    from flake_analysis.api.schemas.compute import BackgroundParams
    p = BackgroundParams(random_seed=7, sample_size=50, blur_strength=8.0, aggregation="median")
    assert p.random_seed == 7
    assert p.aggregation == "median"

def test_background_accepts_legacy_aliases():
    from flake_analysis.api.schemas.compute import BackgroundParams
    p = BackgroundParams(seed=7, max_images=50, gaussian_sigma=8.0, method="median")
    assert p.random_seed == 7
    assert p.blur_strength == 8.0
```
- [ ] Update schema:
```python
class BackgroundParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    random_seed: int = Field(42, validation_alias=AliasChoices("random_seed", "seed"))
    sample_size: int = Field(50, validation_alias=AliasChoices("sample_size", "max_images"))
    blur_strength: float = Field(10.0, validation_alias=AliasChoices("blur_strength", "gaussian_sigma"))
    aggregation: str = Field("median", validation_alias=AliasChoices("aggregation", "method"))
```
- [ ] Run tests — green
- [ ] Commit:
```
git add src/flake_analysis/api/schemas/compute.py tests/api/schemas/test_compute_schemas.py
git commit -m "feat(api): rename BackgroundParams fields with legacy aliases"
```

### Task 1.4 — SamParams + DomainStatsParams + DomainProximityParams rename

**Files:**
- `src/flake_analysis/api/schemas/compute.py`
- `tests/api/schemas/test_compute_schemas.py`

**Steps:**
- [ ] Add failing tests for each:
```python
def test_sam_uses_model_field_not_weights_path():
    from flake_analysis.api.schemas.compute import SamParams
    p = SamParams(model="s3://qpress-uploads/internal/sam/sam_vit_h.pt")
    assert p.model == "s3://qpress-uploads/internal/sam/sam_vit_h.pt"

def test_sam_rejects_device_field():
    import pytest
    from pydantic import ValidationError
    from flake_analysis.api.schemas.compute import SamParams
    with pytest.raises(ValidationError):
        SamParams(model="s3://...", device="cuda")

def test_domain_stats_rejects_repr_mode_and_raw_ext():
    import pytest
    from pydantic import ValidationError
    from flake_analysis.api.schemas.compute import DomainStatsParams
    with pytest.raises(ValidationError):
        DomainStatsParams(repr_mode="mean")
    with pytest.raises(ValidationError):
        DomainStatsParams(raw_ext=".png")

def test_domain_proximity_renames():
    from flake_analysis.api.schemas.compute import DomainProximityParams
    p = DomainProximityParams(
        neighbor_search_radius_px=200.0,
        min_flake_area_px=10,
        max_flake_area_px=10000,
        touch_threshold_px=2.0,
        cluster_link_distance_px=10.0,
        parallel_workers=4,
    )
    assert p.neighbor_search_radius_px == 200.0
    assert p.cluster_link_distance_px == 10.0

def test_domain_proximity_rejects_um_fields():
    import pytest
    from pydantic import ValidationError
    from flake_analysis.api.schemas.compute import DomainProximityParams
    with pytest.raises(ValidationError):
        DomainProximityParams(pixel_size_um=0.5, cluster_link_distance_px=10.0)
    with pytest.raises(ValidationError):
        DomainProximityParams(link_distance_um=5.0, cluster_link_distance_px=10.0)
```
- [ ] Update schemas:
```python
class SamParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    model: str = Field(..., validation_alias=AliasChoices("model", "weights_path"))
    # device removed — server auto-detects

class DomainStatsParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    # repr_mode removed (fixed to median), raw_ext removed (from manifest)
    pass  # all params removed; keep class for symmetry

class DomainProximityParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    neighbor_search_radius_px: float = Field(200.0, validation_alias=AliasChoices("neighbor_search_radius_px", "r_max_px"))
    min_flake_area_px: int = Field(10, validation_alias=AliasChoices("min_flake_area_px", "min_area_px"))
    max_flake_area_px: int = Field(1_000_000, validation_alias=AliasChoices("max_flake_area_px", "max_area_px"))
    touch_threshold_px: float = Field(2.0, validation_alias=AliasChoices("touch_threshold_px", "d_touch_px"))
    cluster_link_distance_px: float = Field(10.0, validation_alias=AliasChoices("cluster_link_distance_px", "link_distance_px"))
    parallel_workers: int = Field(4, validation_alias=AliasChoices("parallel_workers", "workers"))
    # pixel_size_um, link_distance_um removed
```
- [ ] Run tests — green
- [ ] Commit:
```
git add src/flake_analysis/api/schemas/compute.py tests/api/schemas/test_compute_schemas.py
git commit -m "feat(api): rename SAM/DomainStats/DomainProximity params, drop um fields"
```

### Task 1.5 — Wire route handlers + wrappers

**Files:**
- `src/flake_analysis/api/routes/run.py`
- `src/flake_analysis/api/routes/run_pipeline.py`
- `src/flake_analysis/pipeline/thumbnails.py`
- `src/flake_analysis/pipeline/domain_stats.py`
- `src/flake_analysis/pipeline/domain_proximity.py`
- `src/flake_analysis/pipeline/sam.py`

**Steps:**
- [ ] Add failing integration test:
```python
# tests/api/routes/test_run_routes_rename.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_thumbnails_route_resolves_raw_ext_from_manifest(client: AsyncClient, seeded_project):
    resp = await client.post(
        f"/projects/{seeded_project.id}/run/thumbnails",
        json={"thumbnail_quality": 85, "regenerate_cache": False},
    )
    assert resp.status_code == 200
    # raw_ext was sourced from manifest, not request

@pytest.mark.asyncio
async def test_proximity_route_uses_project_pixel_size(client: AsyncClient, seeded_project):
    # project has pixel_size_um=0.5 set
    resp = await client.post(
        f"/projects/{seeded_project.id}/run/domain_proximity",
        json={
            "neighbor_search_radius_px": 200.0,
            "min_flake_area_px": 10,
            "max_flake_area_px": 1000,
            "touch_threshold_px": 2.0,
            "cluster_link_distance_px": 10.0,
            "parallel_workers": 2,
        },
    )
    assert resp.status_code == 200
```
- [ ] In `run.py` thumbnails handler: read manifest, pull raw_ext, pass to wrapper:
```python
manifest = await get_manifest(project_id)
raw_ext = manifest.raw_ext
await thumbnails_wrapper.run(
    project_id=project_id,
    raw_ext=raw_ext,
    quality=params.thumbnail_quality,
    force_recompute=params.regenerate_cache,
)
```
- [ ] In `pipeline/domain_stats.py` wrapper: hardcode `repr_mode="median"`; resolve `raw_ext` from manifest arg
- [ ] In `pipeline/domain_proximity.py` wrapper: drop μm fields entirely; map new names → core kwargs
- [ ] In `pipeline/sam.py` wrapper: accept `model` (S3 URI), resolve to local cached path, pass `weights_path=local_path, device="auto"` to core
- [ ] Run tests — green
- [ ] Commit:
```
git add src/flake_analysis/api/routes/ src/flake_analysis/pipeline/ tests/api/routes/test_run_routes_rename.py
git commit -m "feat(api): wire renamed pipeline params through route handlers and wrappers"
```

---

## Phase 2: pixel_size_um migration (db-specialist)

### Task 2.1 — Failing test for `projects.pixel_size_um` column

**Files:**
- `tests/db/test_projects_pixel_size.py` (create)

**Steps:**
- [ ] Write the test:
```python
import pytest
from decimal import Decimal
from sqlalchemy import select
from flake_analysis.db.models.projects import Project

@pytest.mark.asyncio
async def test_project_has_pixel_size_um_column(db_session, seeded_project):
    seeded_project.pixel_size_um = Decimal("0.5000")
    await db_session.commit()
    result = await db_session.execute(select(Project).where(Project.id == seeded_project.id))
    p = result.scalar_one()
    assert p.pixel_size_um == Decimal("0.5000")

@pytest.mark.asyncio
async def test_project_pixel_size_um_nullable(db_session, seeded_project):
    seeded_project.pixel_size_um = None
    await db_session.commit()  # must not raise
```
- [ ] Run — fails (column doesn't exist)

### Task 2.2 — Add column to ORM model

**Files:**
- `src/flake_analysis/db/models/projects.py` (path verified by Phase 0 audit)

**Steps:**
- [ ] Add field:
```python
from decimal import Decimal
from sqlalchemy import Numeric
from sqlalchemy.orm import Mapped, mapped_column

class Project(Base):
    # ... existing fields
    pixel_size_um: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
```
- [ ] Commit (without migration yet, so test still fails on schema diff — next task)

### Task 2.3 — Alembic migration with data backfill

**Files:**
- `alembic/versions/0007_pipeline_params_refactor.py` (new)

**Steps:**
- [ ] Write migration:
```python
"""pipeline_params_refactor: add projects.pixel_size_um and rewrite pipeline_params blobs"""
from alembic import op
import sqlalchemy as sa

revision = "0007_pipeline_params_refactor"
down_revision = "0006_procrastinate_init"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("pixel_size_um", sa.Numeric(10, 4), nullable=True),
    )

    # Backfill: extract pixel_size_um from any analyses.pipeline_params blob and copy to project.
    # Use the most-recent run per project as the source.
    op.execute("""
        WITH latest AS (
            SELECT DISTINCT ON (project_id)
                project_id,
                (pipeline_params -> 'domain_proximity' ->> 'pixel_size_um')::numeric AS px
            FROM analyses
            WHERE pipeline_params -> 'domain_proximity' ? 'pixel_size_um'
            ORDER BY project_id, created_at DESC
        )
        UPDATE projects p
        SET pixel_size_um = l.px
        FROM latest l
        WHERE p.id = l.project_id AND l.px IS NOT NULL;
    """)

    # Strip um fields from stored pipeline_params blobs
    op.execute("""
        UPDATE analyses
        SET pipeline_params = jsonb_set(
            pipeline_params,
            '{domain_proximity}',
            (pipeline_params -> 'domain_proximity') - 'pixel_size_um' - 'link_distance_um'
        )
        WHERE pipeline_params -> 'domain_proximity' ?| ARRAY['pixel_size_um','link_distance_um'];
    """)

def downgrade() -> None:
    # Reverse: copy projects.pixel_size_um back into the latest analysis blob (best-effort).
    op.execute("""
        UPDATE analyses a
        SET pipeline_params = jsonb_set(
            COALESCE(pipeline_params, '{}'::jsonb),
            '{domain_proximity,pixel_size_um}',
            to_jsonb(p.pixel_size_um)
        )
        FROM projects p
        WHERE a.project_id = p.id AND p.pixel_size_um IS NOT NULL;
    """)
    op.drop_column("projects", "pixel_size_um")
```
- [ ] Run upgrade in test fixture; tests from 2.1 pass
- [ ] Test idempotency: run upgrade twice — second run is no-op (column exists check); document this as a known limitation if Postgres errors
- [ ] Test reversibility: upgrade → downgrade → upgrade round-trip preserves data
- [ ] Commit:
```
git add alembic/versions/0007_pipeline_params_refactor.py src/flake_analysis/db/models/projects.py tests/db/test_projects_pixel_size.py
git commit -m "feat(db): add projects.pixel_size_um and migrate from pipeline_params"
```

### Task 2.4 — API exposure of project.pixel_size_um

**Files:**
- `src/flake_analysis/api/schemas/projects.py`
- `src/flake_analysis/api/routes/projects.py`

**Steps:**
- [ ] Add `pixel_size_um: Decimal | None` to project response schema
- [ ] Add PATCH endpoint (or extend existing) to update it
- [ ] Test:
```python
@pytest.mark.asyncio
async def test_patch_project_pixel_size(client, seeded_project):
    resp = await client.patch(f"/projects/{seeded_project.id}", json={"pixel_size_um": "0.5"})
    assert resp.status_code == 200
    assert resp.json()["pixel_size_um"] == "0.5000"
```
- [ ] Commit:
```
git add src/flake_analysis/api/schemas/projects.py src/flake_analysis/api/routes/projects.py tests/api/routes/test_projects.py
git commit -m "feat(api): expose project.pixel_size_um via projects endpoint"
```

---

## Phase 3: GET /sam/models endpoint (api-developer)

### Task 3.1 — Failing test with moto S3

**Files:**
- `tests/api/services/test_sam_models_service.py` (create)
- `tests/api/routes/test_sam_models_route.py` (create)

**Steps:**
- [ ] Service test:
```python
import boto3
import pytest
from moto import mock_aws
from flake_analysis.api.services.sam_models_service import list_models

@mock_aws
@pytest.mark.asyncio
async def test_list_models_returns_pt_files():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="qpress-uploads")
    s3.put_object(Bucket="qpress-uploads", Key="internal/sam/sam_vit_h.pt", Body=b"x")
    s3.put_object(Bucket="qpress-uploads", Key="internal/sam/sam_vit_l.pt", Body=b"x")
    s3.put_object(Bucket="qpress-uploads", Key="internal/sam/readme.txt", Body=b"x")  # ignored

    models = await list_models()
    names = {m["name"] for m in models}
    assert names == {"sam_vit_h", "sam_vit_l"}
    assert all(m["s3_uri"].startswith("s3://qpress-uploads/internal/sam/") for m in models)
```
- [ ] Route test:
```python
@mock_aws
@pytest.mark.asyncio
async def test_get_sam_models_endpoint(client):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="qpress-uploads")
    s3.put_object(Bucket="qpress-uploads", Key="internal/sam/sam_vit_h.pt", Body=b"x")
    resp = await client.get("/sam/models")
    assert resp.status_code == 200
    body = resp.json()
    assert "models" in body
    assert body["models"][0]["name"] == "sam_vit_h"
```

### Task 3.2 — Implement service

**Files:**
- `src/flake_analysis/api/services/sam_models_service.py` (new)

**Steps:**
- [ ] Implementation:
```python
from typing import Any
import boto3

BUCKET = "qpress-uploads"
PREFIX = "internal/sam/"

async def list_models() -> list[dict[str, Any]]:
    s3 = boto3.client("s3")
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
    out: list[dict[str, Any]] = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if not key.endswith(".pt"):
            continue
        stem = key[len(PREFIX):].removesuffix(".pt")
        out.append({"name": stem, "s3_uri": f"s3://{BUCKET}/{key}"})
    return out
```

### Task 3.3 — Implement route

**Files:**
- `src/flake_analysis/api/routes/sam_models.py` (new)
- `src/flake_analysis/api/routes/__init__.py`

**Steps:**
- [ ] Schema:
```python
# src/flake_analysis/api/schemas/sam.py
from pydantic import BaseModel

class SamModel(BaseModel):
    name: str
    s3_uri: str

class SamModelsResponse(BaseModel):
    models: list[SamModel]
```
- [ ] Route:
```python
# src/flake_analysis/api/routes/sam_models.py
from fastapi import APIRouter, HTTPException
from flake_analysis.api.schemas.sam import SamModelsResponse
from flake_analysis.api.services.sam_models_service import list_models

router = APIRouter(prefix="/sam", tags=["sam"])

@router.get("/models", response_model=SamModelsResponse)
async def get_sam_models() -> SamModelsResponse:
    try:
        models = await list_models()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to list SAM models") from e
    return SamModelsResponse(models=models)
```
- [ ] Register in `routes/__init__.py`
- [ ] Run tests — green
- [ ] Commit:
```
git add src/flake_analysis/api/services/sam_models_service.py src/flake_analysis/api/routes/sam_models.py src/flake_analysis/api/schemas/sam.py src/flake_analysis/api/routes/__init__.py tests/api/services/test_sam_models_service.py tests/api/routes/test_sam_models_route.py
git commit -m "feat(api): add GET /sam/models enumerating S3 SAM checkpoints"
```

---

## Phase 4: Frontend (frontend-architect)

### Task 4.1 — Failing test for renamed fields

**Files:**
- `web/src/components/run/__tests__/PipelineParamsForm.test.tsx`

**Steps:**
- [ ] Add failing tests:
```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PipelineParamsForm } from "../PipelineParamsForm";

test("renders new field labels", () => {
  render(<PipelineParamsForm onSubmit={() => {}} />);
  expect(screen.getByLabelText(/Thumbnail quality/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Regenerate cache/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Random seed/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Sample size/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Blur strength/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Aggregation/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Neighbor search radius/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/raw_ext/i)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Device/i)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/repr.mode/i)).not.toBeInTheDocument();
});
```

### Task 4.2 — SAM model dropdown

**Files:**
- `web/src/api/sam.ts` (new)
- `web/src/components/run/PipelineParamsForm.tsx`

**Steps:**
- [ ] API client:
```ts
// web/src/api/sam.ts
import { httpClient } from "./client";

export type SamModel = { name: string; s3_uri: string };

export async function fetchSamModels(): Promise<SamModel[]> {
  const resp = await httpClient.get<{ models: SamModel[] }>("/sam/models");
  return resp.data.models;
}
```
- [ ] In `PipelineParamsForm.tsx`, add `useEffect` to fetch models on mount; render dropdown:
```tsx
const [models, setModels] = useState<SamModel[]>([]);
const [modelsError, setModelsError] = useState<string | null>(null);

useEffect(() => {
  fetchSamModels()
    .then(setModels)
    .catch(() => setModelsError("Could not load models — retry"));
}, []);

// in JSX
{modelsError ? (
  <span className="error-pill" role="alert">{modelsError}</span>
) : (
  <select name="model" disabled={models.length === 0}>
    <option value="">Select a SAM model…</option>
    {models.map((m) => (
      <option key={m.s3_uri} value={m.s3_uri}>{m.name}</option>
    ))}
  </select>
)}
```
- [ ] Test for dropdown population + error path:
```tsx
test("populates model dropdown from /sam/models", async () => {
  server.use(rest.get("/sam/models", (req, res, ctx) =>
    res(ctx.json({ models: [{ name: "sam_vit_h", s3_uri: "s3://q/internal/sam/sam_vit_h.pt" }] }))
  ));
  render(<PipelineParamsForm onSubmit={() => {}} />);
  await waitFor(() => expect(screen.getByRole("option", { name: "sam_vit_h" })).toBeInTheDocument());
});

test("shows error pill on /sam/models failure", async () => {
  server.use(rest.get("/sam/models", (req, res, ctx) => res(ctx.status(500))));
  render(<PipelineParamsForm onSubmit={() => {}} />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Could not load models/i));
});
```

### Task 4.3 — Remove pixel_size_um from form, read from project metadata

**Files:**
- `web/src/components/run/PipelineParamsForm.tsx`
- `web/src/components/run/PipelineParamsForm.test.tsx`

**Steps:**
- [ ] Remove `pixel_size_um` and `link_distance_um` form fields
- [ ] Add prop `pixelSizeUm: number | null` (passed by parent from project metadata) — used only for μm display in helper text, not as a form field
- [ ] Test:
```tsx
test("does not render pixel_size_um field", () => {
  render(<PipelineParamsForm onSubmit={() => {}} pixelSizeUm={0.5} />);
  expect(screen.queryByLabelText(/Pixel size/i)).not.toBeInTheDocument();
});

test("displays μm conversion helper when pixelSizeUm provided", async () => {
  render(<PipelineParamsForm onSubmit={() => {}} pixelSizeUm={0.5} />);
  await userEvent.type(screen.getByLabelText(/Cluster link distance.*px/i), "10");
  expect(screen.getByText(/= 5.0 μm/i)).toBeInTheDocument();
});
```

### Task 4.4 — Submit payload uses new field names

**Files:**
- `web/src/components/run/PipelineParamsForm.tsx`

**Steps:**
- [ ] Verify submit handler emits new field names (no legacy aliases). Test:
```tsx
test("submits payload with new field names", async () => {
  const onSubmit = vi.fn();
  render(<PipelineParamsForm onSubmit={onSubmit} pixelSizeUm={0.5} />);
  await userEvent.click(screen.getByRole("button", { name: /Run/i }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    thumbnails: expect.objectContaining({ thumbnail_quality: expect.any(Number), regenerate_cache: expect.any(Boolean) }),
    background: expect.objectContaining({ random_seed: expect.any(Number), sample_size: expect.any(Number), blur_strength: expect.any(Number), aggregation: expect.any(String) }),
    sam: expect.objectContaining({ model: expect.any(String) }),
    domain_proximity: expect.objectContaining({
      neighbor_search_radius_px: expect.any(Number),
      cluster_link_distance_px: expect.any(Number),
      parallel_workers: expect.any(Number),
    }),
  }));
  expect(onSubmit.mock.calls[0][0].sam).not.toHaveProperty("device");
  expect(onSubmit.mock.calls[0][0].domain_proximity).not.toHaveProperty("pixel_size_um");
});
```
- [ ] Commit:
```
git add web/src/components/run/PipelineParamsForm.tsx web/src/components/run/__tests__/PipelineParamsForm.test.tsx web/src/api/sam.ts
git commit -m "feat(web): refactor PipelineParamsForm with renamed fields and SAM model dropdown"
```

---

## Phase 5: Polish (code-reviewer)

### Task 5.1 — Remove Pydantic Field aliases

**Files:**
- `src/flake_analysis/api/schemas/compute.py`

**Steps:**
- [ ] Replace each `validation_alias=AliasChoices(...)` with simple `Field(default)`. Legacy field names now rejected.
- [ ] Update tests that previously asserted legacy aliases work — flip them to assert legacy names produce ValidationError
- [ ] Run full test sweep
- [ ] Commit:
```
git add src/flake_analysis/api/schemas/compute.py tests/api/schemas/test_compute_schemas.py
git commit -m "refactor(api): remove legacy field aliases after deprecation cycle"
```

### Task 5.2 — Regenerate API docs + project status

**Files:**
- `docs/project-status.md`
- (any auto-generated OpenAPI snapshot if checked in)

**Steps:**
- [ ] Update `project-status.md` to mark refactor complete; link to this plan
- [ ] Run code-reviewer pass over the diff (BLOCKER/SUGGESTION/NIT triage)
- [ ] Commit:
```
git add docs/project-status.md
git commit -m "docs(status): mark pipeline params refactor complete"
```

### Task 5.3 — Final test sweep

**Steps:**
- [ ] Backend: full pytest run, including parity tests (delegate to api-developer)
- [ ] Frontend: full vitest + lint (delegate to frontend-architect)
- [ ] Confirm no remaining references to old field names: `grep -rn "raw_ext\|force_recompute\|weights_path\|repr_mode\|gaussian_sigma\|r_max_px\|min_area_px\|max_area_px\|d_touch_px\|link_distance_um" src/ web/src/` — empty result expected
- [ ] If any audit hit not covered by Phase 1.5, raise to PM before marking phase complete

---

## Self-review

- [ ] All 8 owner items covered by at least one task
- [ ] No "TODO: implement" or "TBD" placeholders
- [ ] Every task has actual test code (not "write tests for the above")
- [ ] Every renamed field has a Field alias OR a clear breaking-change note
- [ ] alembic migration is idempotent and reversible
- [ ] Frontend dropdown error path (S3 list fail) is specified
- [ ] All file paths verified to exist
