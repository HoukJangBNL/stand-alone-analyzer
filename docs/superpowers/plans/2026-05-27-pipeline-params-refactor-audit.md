# Pipeline Params Refactor — Call Site Audit

> Phase 0 output for [the refactor plan](2026-05-27-pipeline-params-refactor.md). Read-only enumeration; no code changes.
> Owner-approved scope: AD1 project-level `pixel_size_um`, AD2 API-boundary-only rename, AD3 `AliasChoices` 1 cycle, AD4 S3 SAM models.

## Methodology

Greps run from repo root:
```
grep -rnE "raw_ext|force_recompute|weights_path|repr_mode|gaussian_sigma|r_max_px|min_area_px|max_area_px|d_touch_px|link_distance_um|link_distance_px|pixel_size_um|max_images" src/ web/src/ tests/ docs/
grep -rnE "\bworkers\b" src/ web/src/ tests/   # filtered for pipeline context
grep -rnE "\bquality\b"  src/ web/src/ tests/   # filtered for thumbnail context
```

Layer mapping used (matches existing 4-layer structure):
- **Schema** = `src/flake_analysis/api/schemas/compute.py`
- **Route** = `src/flake_analysis/api/routes/run.py` (legacy per-step endpoints) + `run_pipeline.py` (composite)
- **Wrapper** = `src/flake_analysis/pipeline/<step>.py` (thin async wrapper around core)
- **Core** = `src/flake_analysis/core/pipeline/<step>.py` plus the lower-level helpers in `core/image_processing/*.py`, `core/color_classification/*.py`, `core/annotations/*.py`
- **DB ORM** = `src/flake_analysis/db/models/*.py` (Analysis, Image, UploadItem)
- **UI** = `web/src/**`
- **Test** = `tests/**`
- **Doc** = `docs/**`

Per AD2, **only Schema + Route layers rename** (and UI + tests that talk to those). Wrapper layer translates new schema names back to existing core kwargs. Core kwargs stay untouched.

## Summary table

Counts are *file:line hits*, not unique files. UI column counts only `web/src/` runtime hits (excludes `__tests__` which we list under Test).

| Field | Schema | Route | Wrapper | Core | DB ORM | UI | Test | Doc | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `raw_ext` | 2 | 5 | 5 | 13 | 0 | 0 | 13 | 19 | 57 |
| `force_recompute` | 1 | 2 | 2 | 9 | 0 | 0 | 3 | 7 | 24 |
| `weights_path` | 1 | 6 | 2 | 2 | 0 | 0 | 14 | 17 | 42 |
| `repr_mode` | 1 | 4 | 3 | 8 | 0 | 0 | 16 | 11 | 43 |
| `gaussian_sigma` | 1 | 2 | 3 | 9 | 0 | 0 | 17 | 8 | 40 |
| `r_max_px` | 1 | 4 | 3 | 6 | 0 | 0 | 9 | 9 | 32 |
| `min_area_px` | 1 | 2 | 3 | 5 | 1 | 0 | 16 | 12 | 40 |
| `max_area_px` | 1 | 2 | 3 | 5 | 1 | 0 | 4 | 8 | 24 |
| `d_touch_px` | 1 | 2 | 3 | 8 | 0 | 0 | 9 | 8 | 31 |
| `link_distance_um` | 1 | 2 | 3 | 7 | 0 | 0 | 11 | 7 | 31 |
| `link_distance_px` | 0 | 0 | 0 | 2 | 1 | 0 | 9 | 5 | 17 |
| `pixel_size_um` | 1 | 2 | 3 | 8 | 2 | 0 | 13 | 16 | 45 |
| `max_images` | 1 | 3 | 3 | 11 | 0 | 0 | 24 | 7 | 49 |
| `workers` (pipeline) | 1 | 4 | 3 | 5 | 0 | 0 | 13 | 0 | 26 |
| `quality` (thumbnail) | 1 | 2 | 2 | 6 | 0 | 0 | 13 | 11 | 35 |

Total non-doc, non-test code hits across all fields: **~110 in src/**, plus **~245 in tests/** and **1 in `web/src/hooks/__tests__`**. Frontend runtime code (`web/src/**` excluding tests) has **zero** of the renamed fields — `StepCard.tsx` currently sends `params={}` only. UI work for Phase 3 will be additive, not migratory.

## Per-field details

### `raw_ext`  *(remove — resolved from manifest)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:14` (`ThumbnailsParams.raw_ext`), `:44` (`DomainStatsParams.raw_ext`)
- **Route**: `src/flake_analysis/api/routes/run.py:70`, `:241`, `:258`; `src/flake_analysis/api/routes/run_pipeline.py:283`, `:409`, `:418` — passed to wrapper, also embedded in metrics dict
- **Wrapper**: `src/flake_analysis/pipeline/thumbnails.py:32`, `:49`; `src/flake_analysis/pipeline/domain_stats.py:25`, `:46`, `:55` — wrappers must drop the kwarg from API, derive it from manifest, then forward to core
- **Core**: `src/flake_analysis/core/pipeline/thumbnails.py:180`, `:199`, `:229`, `:231`, `:343`; `src/flake_analysis/core/pipeline/domain_stats.py:52`, `:75`, `:164`, `:203`, `:249`; `src/flake_analysis/core/color_classification/loader.py:186`, `:211`, `:287`; `src/flake_analysis/core/annotations/annotation_loader.py:315`, `:330`, `:379` — core kwarg stays per AD2; wrapper supplies it
- **DB ORM**: none (lives in `analyses.proximity_params` JSONB only — no column)
- **UI**: none in `web/src/**`
- **Test**: `tests/api/test_compute_schemas.py:13`; `tests/api/test_run_pipeline_sse.py:183`, `:186`; `tests/api/test_run_emits_usage.py:101`; `tests/api/test_run_domain_stats_sse.py:140`, `:149`, `:181`, `:219`, `:339`, `:347`, `:365`; `tests/test_pipeline_selector.py:49` — body payloads carrying `raw_ext` must move to legacy alias or be dropped
- **Doc**: `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:68`, `:84`, `:169`; `docs/superpowers/specs/2026-05-20-react-fastapi-migration-design.md:419`, `:420`; `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:1959`, `:2035`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1619`, `:1659`, `:1689`, `:1846`, `:2122`, `:3119`, `:3125`, `:3152`, `:3156`; `docs/superpowers/specs/2026-05-20-backend-design.md:141`, `:153`; `docs/superpowers/specs/2026-05-20-frontend-design.md:235`; `docs/superpowers/specs/2026-05-20-mosaic-viewer-design.md:174`–`175`, `:527`, `:534`, `:633`, `:675`–`:677`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:115`, `:117` — historical specs; not load-bearing for new code, but note for archival sanity
- **Migration risk**: `analyses.amg_params` / `proximity_params` / `background_params` are JSONB; check if any persisted blob ever stored `raw_ext`. Code path embeds `raw_ext` in `metrics` dict (`run_pipeline.py:418`) which lands in a `runs.metrics` JSONB-style field. Phase 1 may need a backfill or read-tolerant accessor.

### `force_recompute`  *(rename → `regenerate_cache`)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:16` (`ThumbnailsParams.force_recompute`)
- **Route**: `src/flake_analysis/api/routes/run.py:72`; `src/flake_analysis/api/routes/run_pipeline.py:285`
- **Wrapper**: `src/flake_analysis/pipeline/thumbnails.py:34`, `:51` — translate `regenerate_cache` → `force_recompute` here
- **Core**: `src/flake_analysis/core/pipeline/thumbnails.py:18` (docstring), `:182`, `:204`, `:252`, `:279`; `src/flake_analysis/core/color_classification/loader.py:185`, `:209`, `:231`, `:282`; `src/flake_analysis/core/pipeline/domain_stats.py:202` — internal kwarg stays per AD2
- **DB ORM**: none
- **UI**: none
- **Test**: `tests/api/test_compute_schemas.py:15`; `tests/api/test_run_pipeline_sse.py:183`; `tests/api/test_run_emits_usage.py:103`
- **Doc**: `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:68`; `docs/superpowers/specs/2026-05-20-mosaic-viewer-design.md:145`; `docs/superpowers/specs/2026-05-20-backend-design.md:143`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1621`, `:1661`, `:1848`, `:3121`, `:3125`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:115`

### `weights_path`  *(rename → `model`, semantics: SAM model display name resolved via S3 per AD4)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:74` (`SamParams.weights_path`)
- **Route**: `src/flake_analysis/api/routes/run.py:308`, `:338`, `:408`; `src/flake_analysis/api/routes/run_pipeline.py:68` (`SamParams` field comment), `:172`, `:189`, `:340`
- **Wrapper**: `src/flake_analysis/pipeline/sam.py:17`, `:24` — accepts `model` (display name), resolves to S3 → local path, passes to core as `weights_path`
- **Core**: `src/flake_analysis/core/pipeline/sam.py:27`, `:53` — kwarg stays
- **DB ORM**: none directly. SAM model registry lives in `db/models/sam.py` (Model table) — may need a `display_name` field check
- **UI**: none in `web/src/**` runtime
- **Test**: `tests/pipeline/test_sam_step.py:21`, `:30`; `tests/core/pipeline/test_sam_engine.py:18`, `:29`; `tests/api/test_run_sam_sse.py:165`, `:209`, `:243`, `:326`; `tests/api/test_run_pipeline_sse.py:185`, `:296`, `:412`; `tests/worker/test_tasks.py:57`, `:62`, `:102`, `:110`, `:124`, `:133`, `:172`; `tests/e2e/pipeline.spec.ts:230`
- **Doc**: heavy use in `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md` (~13 lines: `:185`, `:217`, `:246`, `:261`, `:322`, `:383`, `:451`, `:482`, `:872`, `:883`, `:930`, `:954`, `:1013`, `:1045`, `:1052`, `:1389`, `:1419`, `:1478`, `:1692`, `:1696`, `:1705`, `:2037`)
- **Worker layer**: `src/flake_analysis/worker/tasks.py:100`, `:134` — RQ task signature carries `weights_path: str`. Phase 1 must decide whether worker boundary uses new `model` name or stays as `weights_path` (worker is not user-facing API)

### `repr_mode`  *(remove — hardcode to `median`)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:43` (`DomainStatsParams.repr_mode`)
- **Route**: `src/flake_analysis/api/routes/run.py:240`, `:257`; `src/flake_analysis/api/routes/run_pipeline.py:408`, `:417`
- **Wrapper**: `src/flake_analysis/pipeline/domain_stats.py:24`, `:45`, `:54` — wrapper hardcodes `"median"` once schema drops it
- **Core**: `src/flake_analysis/core/pipeline/domain_stats.py:6`, `:51`, `:73`, `:96`, `:97`, `:117`, `:201`, `:248` — keeps the validation `if repr_mode not in ("median", "mean")`; core stays per AD2
- **DB ORM**: none
- **UI**: none
- **Test**: `tests/test_pipeline_clustering.py:76`; `tests/test_progress_callback_passthrough.py:93`; `tests/test_pipeline_selector.py:49`; `tests/parity/test_pipeline_e2e.py:65`; `tests/parity/regenerate_golden.py:86`; `tests/api/test_run_pipeline_sse.py:186`; `tests/api/test_run_domain_stats_sse.py` — 9 lines (`:140`, `:149`, `:181`, `:189`, `:219`, `:227`, `:242`, `:271`, `:339`, `:347`, `:365`)
- **Doc**: `docs/superpowers/specs/2026-05-20-backend-design.md:152`; `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:84`, `:291`; `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:2038`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1688`, `:2066`, `:2072`, `:2121`, `:3151`, `:3156`; `docs/superpowers/specs/2026-05-20-react-fastapi-migration-design.md:343`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:117`
- **Migration risk**: `runs.metrics` JSONB will have `repr_mode: "median"` for existing rows — readers must tolerate field absence going forward.

### `gaussian_sigma`  *(rename TBD per plan; appears unrenamed in scope but was in audit list — verify with plan)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:32` (`BackgroundParams.gaussian_sigma`)
- **Route**: `src/flake_analysis/api/routes/run.py:148`; `src/flake_analysis/api/routes/run_pipeline.py:304`
- **Wrapper**: `src/flake_analysis/pipeline/background.py:29`, `:45`, `:54`
- **Core**: `src/flake_analysis/core/pipeline/background.py:35`, `:56`, `:84`, `:105`; `src/flake_analysis/core/image_processing/background.py:20`, `:43`, `:141`, `:142`, `:144`, `:147`, `:149`
- **DB ORM**: stored in `analyses.background_params` JSONB
- **UI**: none
- **Test**: `tests/test_progress_callback_passthrough.py:65`, `:211`; `tests/core/test_progress_callback.py:65`, `:85`; `tests/core/test_background_seed.py:28`, `:31`, `:48`, `:51`, `:64`; `tests/core/test_pipeline_smoke.py:45`; `tests/api/test_run_emits_usage.py:178`; `tests/api/test_run_pipeline_sse.py:397`, `:411`, `:458`; `tests/api/test_run_background_sse.py:127`, `:138`, `:170`, `:208`, `:341`; `tests/api/test_run_domain_stats_sse.py:54`
- **Doc**: `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:1298`; `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:78`; `docs/superpowers/specs/2026-05-20-backend-design.md:148`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1677`, `:1985`, `:3136`, `:3141`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:116`
- **Note**: The plan brief lists `gaussian_sigma` as an audit field but does NOT explicitly state a rename target. **PM should confirm** whether this is being renamed (e.g., `smoothing_sigma`?) or kept. If kept, this field's audit is informational only.

### `r_max_px`  *(rename → `interaction_radius_px`)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:54` (`DomainProximityParams.r_max_px`)
- **Route**: `src/flake_analysis/api/routes/run.py:537`, `:559`; `src/flake_analysis/api/routes/run_pipeline.py:427`, `:441`
- **Wrapper**: `src/flake_analysis/pipeline/domain_proximity.py:26`, `:43`, `:55` — translate `interaction_radius_px` → core's `r_max_px`
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:62`, `:79`, `:110`, `:138`, `:229`; `src/flake_analysis/core/image_processing/pair_distance.py:7`, `:9` (docstring) — kwarg stays
- **DB ORM**: lives in `analyses.proximity_params` JSONB (per `docs/db-schema-v6.md:541`); no column
- **UI**: none
- **Test**: `tests/core/test_progress_callback.py:136`, `:157`; `tests/core/test_pipeline_smoke.py:103`; `tests/api/test_run_pipeline_sse.py:188`; `tests/api/test_run_domain_proximity_sse.py:137`, `:157`, `:201`, `:253`, `:390`, `:426` (the `:426` line asserts `metrics == {"r_max_px": 200.0, "workers": 4}` — direct shape assertion will break on rename)
- **Doc**: `docs/db-schema-v6.md:541`; `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:103`; `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:2039`; `docs/superpowers/specs/2026-05-20-backend-design.md:175`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1699`, `:2196`, `:2202`, `:2255`, `:3165`, `:3175`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:118`

### `min_area_px`  *(KEEP — DB column on `analyses` table; rename only at API boundary if plan dictates)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:55`
- **Route**: `src/flake_analysis/api/routes/run.py:538`; `src/flake_analysis/api/routes/run_pipeline.py:428`
- **Wrapper**: `src/flake_analysis/pipeline/domain_proximity.py:27`, `:44`, `:56`
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:63`, `:81`, `:120`, `:139`, `:230`; `src/flake_analysis/core/image_processing/pair_distance.py:163`, `:177`, `:195`
- **DB ORM**: `src/flake_analysis/db/models/analysis.py:86` (`Analysis.min_area_px` — promoted column per `docs/db-schema-v6.md:28`, `:539`); also `tests/api/services/test_runs.py` and `tests/api/test_deps.py:53` use it as ORM kwarg, **NOT** a body field
- **UI**: none
- **Test**: `tests/test_pipeline_clustering.py:76`; `tests/core/test_pair_distance_smoke.py:26`, `:59`; `tests/core/test_pipeline_smoke.py:104`; `tests/core/test_progress_callback.py:137`, `:158`; `tests/parity/regenerate_golden.py:90`; `tests/parity/test_reproducibility.py:58`; `tests/parity/test_schema_validators.py:51`; `tests/parity/test_pipeline_e2e.py:72`, `:128`, `:175`; `tests/api/test_run_pipeline_sse.py:189`; `tests/api/test_deps.py:53`; `tests/api/test_run_domain_proximity_sse.py:138`, `:158`, `:202`, `:254`, `:391`
- **Doc**: `docs/db-schema-v6.md:28`, `:123`, `:539`; `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:103`; `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:2039`, `:2337`; `docs/superpowers/plans/2026-05-22-W10-B-active-project-decoupling.md:611`; `docs/superpowers/specs/2026-05-20-backend-design.md:176`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1700`, `:2256`, `:3166`, `:3175`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:119`
- **Note**: Plan brief does not explicitly list `min_area_px` as renamed (different from the renamed list `quality/force_recompute/weights_path/r_max_px/link_distance_um`). Audit included it because grep target hit. **PM confirm** whether this stays as-is at API boundary.

### `max_area_px`  *(KEEP — same rationale as `min_area_px`)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:56`
- **Route**: `src/flake_analysis/api/routes/run.py:539`; `src/flake_analysis/api/routes/run_pipeline.py:429`
- **Wrapper**: `src/flake_analysis/pipeline/domain_proximity.py:28`, `:45`, `:57`
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:64`, `:83`, `:120`, `:141`, `:231`; `src/flake_analysis/core/image_processing/pair_distance.py:165`, `:180`, `:192`
- **DB ORM**: `src/flake_analysis/db/models/analysis.py:91` (`Analysis.max_area_px` — promoted column)
- **UI**: none
- **Test**: `tests/api/test_run_domain_proximity_sse.py:139`, `:203`, `:255`, `:392`
- **Doc**: `docs/db-schema-v6.md:28`, `:124`, `:540`; `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:103`; `docs/superpowers/specs/2026-05-20-backend-design.md:177`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1701`, `:2257`, `:3167`, `:3175`

### `d_touch_px`  *(KEEP — likely; audit included for completeness)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:57`
- **Route**: `src/flake_analysis/api/routes/run.py:540`; `src/flake_analysis/api/routes/run_pipeline.py:430`
- **Wrapper**: `src/flake_analysis/pipeline/domain_proximity.py:29`, `:46`, `:58`
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:65`, `:86`, `:92`, `:140`, `:204`, `:232`; `src/flake_analysis/core/image_processing/pair_distance.py:164`, `:178`, `:248`, `:276`, `:278`, `:284`, `:312`
- **DB ORM**: lives in `analyses.proximity_params` JSONB only
- **UI**: none
- **Test**: `tests/core/test_pipeline_smoke.py:105`; `tests/core/test_pair_distance_smoke.py:59`, `:73`; `tests/core/test_progress_callback.py:138`; `tests/parity/regenerate_golden.py:90`; `tests/parity/test_pipeline_e2e.py:72`; `tests/api/test_run_pipeline_sse.py:190`; `tests/api/test_run_domain_proximity_sse.py:140`, `:159`, `:204`, `:256`, `:393`
- **Doc**: `docs/db-schema-v6.md:541`; `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:103`; `docs/superpowers/specs/2026-05-20-backend-design.md:178`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1702`, `:2258`, `:3168`, `:3175`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:118`

### `link_distance_um`  *(rename → `link_distance_px`; semantics shift unit, ALSO collapses with existing DB column)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:59` (`DomainProximityParams.link_distance_um`)
- **Route**: `src/flake_analysis/api/routes/run.py:542`; `src/flake_analysis/api/routes/run_pipeline.py:432`
- **Wrapper**: `src/flake_analysis/pipeline/domain_proximity.py:31`, `:48`, `:59` — wrapper either receives `link_distance_px` and converts to `link_distance_um` for core, OR pushes the unit change down (decision needed)
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:66`, `:89`, `:90`, `:95`, `:200`, `:202`, `:203`, `:233` — kwarg stays per AD2 (the existing core-internal computation already converts `link_distance_um → link_distance_px` at line 206)
- **DB ORM**: none for `_um` flavor. The `_px` flavor IS the canonical column on `analyses` (see next field)
- **UI**: none
- **Test**: `tests/core/test_pipeline_smoke.py:106`; `tests/core/test_progress_callback.py:139`; `tests/parity/regenerate_golden.py:91`; `tests/parity/test_schema_validators.py:51`; `tests/parity/test_reproducibility.py:58`; `tests/parity/test_pipeline_e2e.py:73`, `:128`, `:175`; `tests/api/test_run_pipeline_sse.py:192`; `tests/api/test_run_domain_proximity_sse.py:142`, `:161`, `:206`, `:258`, `:395`
- **Doc**: `docs/db-schema-v6.md:27` (says explicitly "`analyses.link_distance_um` 제거. `link_distance_px NOT NULL` 만 유지"); `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:103`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1704`, `:2260`, `:3170`, `:3175`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:118`; `docs/superpowers/specs/2026-05-20-backend-design.md:180`
- **Migration risk**: schema-v6 already canonicalized to `_px` at DB layer. The API rename to `link_distance_px` finally aligns the boundary with the DB column. Tests in `tests/parity/*` that pass `link_distance_um=1.0, pixel_size_um=0.5` need parallel assertion that the resulting `link_distance_px=2.0` is honored.

### `link_distance_px`  *(canonical DB column; receiving the rename target from `link_distance_um`)*
- **Schema**: not in current schema (will be ADDED)
- **Route**: not in current routes
- **Wrapper**: not in current wrapper
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:206`, `:208` — locally computed, not a kwarg
- **DB ORM**: `src/flake_analysis/db/models/analysis.py:85` (`Analysis.link_distance_px: REAL NOT NULL`)
- **UI**: none
- **Test**: `tests/db/test_analysis_status_generated.py:65`; `tests/db/conftest.py:125`; `tests/api/test_run_pipeline_sse.py:398`; `tests/api/test_run_sam_sse.py:286`; `tests/api/test_deps.py:52`; `tests/api/test_run_background_sse.py:311`; `tests/api/test_run_domain_stats_sse.py:312`; `tests/api/test_run_domain_proximity_sse.py:359`; `tests/api/services/test_runs.py:39` — these test `Analysis` ORM kwargs, NOT the API body field
- **Doc**: `docs/db-schema-v6.md:27`, `:375`, `:538`; `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:2336`; `docs/superpowers/plans/2026-05-22-W10-B-active-project-decoupling.md:611`; `docs/superpowers/plans/2026-05-21-W4.2-db-orm.md:352`
- **Note**: When the API schema gains `link_distance_px`, it will collide-by-name with the existing ORM column. That's intentional alignment — the call sites that already use `link_distance_px` for the ORM kwarg are unchanged.

### `pixel_size_um`  *(remove from API; move to `projects` table per AD1)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:58` (`DomainProximityParams.pixel_size_um`)
- **Route**: `src/flake_analysis/api/routes/run.py:541`; `src/flake_analysis/api/routes/run_pipeline.py:431`
- **Wrapper**: `src/flake_analysis/pipeline/domain_proximity.py:30`, `:47`, `:60` — wrapper resolves from `projects.pixel_size_um` via session-bound active-project lookup, then passes as core kwarg
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:67`, `:92`, `:93`, `:110`, `:194`, `:204`, `:206`, `:234` — kwarg stays
- **DB ORM**: `src/flake_analysis/db/models/upload.py:129` (`Image.pixel_size_um`), `:193` (`UploadItem.pixel_size_um`). **NOT yet on `Project`** — schema migration owed for AD1
- **UI**: none
- **Test**: `tests/core/test_pipeline_smoke.py:107`; `tests/core/test_progress_callback.py:140`; `tests/parity/test_reproducibility.py:58`; `tests/parity/regenerate_golden.py:91`; `tests/parity/test_pipeline_e2e.py:73`, `:128`, `:175`; `tests/parity/test_schema_validators.py:51`; `tests/api/test_run_pipeline_sse.py:191`; `tests/api/test_run_domain_proximity_sse.py:141`, `:160`, `:205`, `:257`, `:394`
- **Doc**: `docs/db-schema-v6.md:26`, `:89`, `:121`, `:260`, `:325`, `:373`–`:374`, `:487`, `:516`, `:728`; `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:103`; `docs/superpowers/plans/2026-05-21-W5-upload-flow.md:61`, `:62`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1703`, `:2259`, `:3169`, `:3175`; `docs/superpowers/specs/2026-05-20-backend-design.md:179`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:119`
- **Migration risk**: `db-schema-v6.md:728` already documents the NULL-fallback rule for `images.pixel_size_um`. AD1 elevates `pixel_size_um` to project-level — verify this doesn't conflict with the existing image-level column. PM should clarify: is project-level *additionally* added (overriding image-level when present), or is image-level being *removed*?

### `max_images`  *(KEEP — likely; audit included for completeness)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:31`
- **Route**: `src/flake_analysis/api/routes/run.py:147`, `:165`; `src/flake_analysis/api/routes/run_pipeline.py:303`, `:313`
- **Wrapper**: `src/flake_analysis/pipeline/background.py:28`, `:44`, `:53`
- **Core**: `src/flake_analysis/core/pipeline/background.py:34`, `:54`, `:61`, `:73`, `:81`, `:104`; `src/flake_analysis/core/image_processing/background.py:17`, `:37`, `:49`, `:96`, `:100`, `:102`, `:104`; `src/flake_analysis/core/color_classification/loader.py:290`
- **DB ORM**: stored in `analyses.background_params` JSONB
- **UI**: none
- **Test**: 24 hits across `tests/test_state_hashing.py`, `tests/test_pipeline_background.py`, `tests/test_progress_callback_passthrough.py`, `tests/test_state_manifest.py`, `tests/core/test_pipeline_smoke.py`, `tests/core/test_background_seed.py`, `tests/core/test_progress_callback.py`, `tests/parity/*`, `tests/api/test_run_pipeline_sse.py`, `tests/api/test_run_emits_usage.py`, `tests/api/test_run_background_sse.py`, `tests/api/test_run_domain_stats_sse.py`
- **Doc**: `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:78`; `docs/superpowers/specs/2026-05-20-backend-design.md:147`; `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:1298`, `:1336`, `:2036`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:1676`, `:1984`, `:3135`, `:3141`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:116`

### `workers` (pipeline-related)  *(KEEP — likely)*
Filter: pipeline-context only; uvicorn/EC2/upload concurrency excluded (see false-positives section).
- **Schema**: `src/flake_analysis/api/schemas/compute.py:60` (`DomainProximityParams.workers`)
- **Route**: `src/flake_analysis/api/routes/run.py:543`, `:560`; `src/flake_analysis/api/routes/run_pipeline.py:433`, `:442`
- **Wrapper**: `src/flake_analysis/pipeline/domain_proximity.py:32`, `:49`, `:61`
- **Core**: `src/flake_analysis/core/pipeline/domain_proximity.py:68`, `:96`, `:150`, `:151`; `src/flake_analysis/core/pipeline/thumbnails.py:210` (docstring), `:302` (log line `max_workers={max_workers}`)
- **DB ORM**: stored in `analyses.proximity_params` JSONB
- **UI**: none
- **Test**: `tests/core/test_progress_callback.py:141`, `:159`; `tests/core/test_pipeline_smoke.py:108`; `tests/parity/regenerate_golden.py:91`; `tests/parity/test_pipeline_e2e.py:73`, `:128`, `:175`; `tests/parity/test_reproducibility.py:58`; `tests/parity/test_schema_validators.py:51`; `tests/api/test_run_pipeline_sse.py:193`; `tests/api/test_run_domain_proximity_sse.py:143`, `:162`, `:207`, `:259`, `:396`, `:426`

### `quality` (thumbnail-related)  *(rename → `thumbnail_quality`)*
- **Schema**: `src/flake_analysis/api/schemas/compute.py:15` (`ThumbnailsParams.quality`)
- **Route**: `src/flake_analysis/api/routes/run.py:71`; `src/flake_analysis/api/routes/run_pipeline.py:284`
- **Wrapper**: `src/flake_analysis/pipeline/thumbnails.py:33`, `:50`
- **Core**: `src/flake_analysis/core/pipeline/thumbnails.py:137`, `:157` (comment), `:162`, `:181`, `:201`, `:202` (docstring), `:316`, `:344` — kwarg stays
- **DB ORM**: stored in (unspecified — likely `runs.metrics` JSONB)
- **UI**: none in `web/src/**` runtime; only `web/src/hooks/__tests__/useStepProgress.test.ts:57` (test-only)
- **Test**: `tests/api/test_compute_schemas.py:14`, `:24`; `tests/api/test_run_pipeline_sse.py:183`; `tests/api/test_run_emits_usage.py:102`; `tests/api/test_errors.py:14`, `:19`; `tests/api/test_sse_heartbeat.py:128`; `tests/api/test_data_manifest.py:65`, `:76`; `tests/api/test_run_thumbnails_sse.py:106`, `:117`, `:173`, `:220`, `:264`, `:285`, `:316`
- **Doc**: `docs/superpowers/research/2026-05-20-codebase-reuse-map.md:68`, `:169`; `docs/superpowers/specs/2026-05-20-backend-design.md:142`; `docs/superpowers/specs/2026-05-20-mosaic-viewer-design.md:147`; `docs/superpowers/plans/2026-05-20-foundation-and-compute-tab.md:207`, `:212`, `:1297`, `:1311`, `:1620`, `:1630`, `:1660`, `:1775`, `:1783`, `:1847`, `:2667`, `:3120`, `:3125`; `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:1959`, `:2035`; `docs/superpowers/plans/2026-05-21-W2-backend.md:782`; `docs/superpowers/specs/2026-05-20-frontend-design.md:235`; `docs/superpowers/requirements/2026-05-20-react-fastapi-migration-requirements.md:115`

## Out-of-scope hits (false positives)

`workers` matches outside pipeline domain:
- `src/flake_analysis/worker/launcher.py:63`, `:233` — EC2 worker fleet (production GPU pool); not a pipeline param
- `tests/worker/test_launcher.py:183` — test of EC2 worker fleet
- `web/src/lib/uploadOrchestrator.ts:74`, `:82`, `:83` — upload concurrency pool (parallel upload promises)

`quality` matches outside thumbnail domain:
- "code quality" / "code-quality" mentions in plan docs (`docs/superpowers/plans/2026-05-22-W6.6-test-b-hotfixes.md:519`, `docs/superpowers/plans/2026-05-26-W12-scan-table-and-delete.md:1445`, `docs/superpowers/plans/2026-05-21-clustering-tab.md:4580`, `docs/superpowers/plans/2026-05-25-segmentation-web-integration.md:1899`, `docs/superpowers/plans/2026-05-21-cutover-streamlit-deletion.md:2345`) — process language, not the field
- `tests/api/test_errors.py:14`, `:19` — uses `field="quality"` as a string label for a generic `ParamsInvalid` error envelope test; technically thumbnail-adjacent since `quality` is the example, but these tests will need updating if the canonical example field name changes

`uvicorn workers`: deploy/systemd config does NOT pass `--workers` to uvicorn (`deploy/systemd/saa-api.service:30`), so no false positive there.

## Phase coverage check

This table assumes the plan has Phases 1–5 in this order: 1=Schema rename + AliasChoices, 2=Wrapper translation, 3=Frontend, 4=SAM/S3, 5=Alias removal. PM to confirm against actual plan file.

| Field | Plan task that touches it | Coverage |
|---|---|---|
| `raw_ext` | Phase 1 (Thumbnails + DomainStats schema/route remove + manifest resolver in wrapper) | ⚠ verify wrapper-side manifest lookup is in plan; route's `metrics` dict embed at `run_pipeline.py:418` may be missed |
| `force_recompute` → `regenerate_cache` | Phase 1 (Thumbnails schema + AliasChoices) | ✅ |
| `weights_path` → `model` | Phase 4 (S3 SAM models AD4) | ⚠ worker boundary (`worker/tasks.py:100`) — confirm this is included in Phase 4 or held back as worker-internal |
| `repr_mode` | Phase 1 (DomainStats schema remove + wrapper hardcode `"median"`) | ✅ |
| `gaussian_sigma` | NOT explicitly in plan brief rename list | ⚠ **AMBIGUOUS** — PM must confirm whether kept or renamed |
| `r_max_px` → `interaction_radius_px` | Phase 1 (DomainProximity schema rename) | ⚠ test `tests/api/test_run_domain_proximity_sse.py:426` asserts `metrics == {"r_max_px": 200.0, "workers": 4}` — direct shape assertion, will break |
| `min_area_px` | NOT in plan rename list | ✅ stays as-is (kept; promoted DB column already aligned) |
| `max_area_px` | NOT in plan rename list | ✅ stays as-is |
| `d_touch_px` | NOT in plan rename list | ✅ stays as-is (PM confirm) |
| `link_distance_um` → `link_distance_px` | Phase 1 (DomainProximity schema rename + unit shift) | ⚠ parity tests pass `link_distance_um=1.0, pixel_size_um=0.5` — Phase 1 must update fixtures or add converter; also coordinate with `pixel_size_um` removal (next row) since the conversion needs `pixel_size_um` |
| `link_distance_px` | Phase 1 (added at API as new field) | ✅ DB column already exists (`analysis.py:85`) |
| `pixel_size_um` | Phase 1 (remove from `DomainProximityParams`) + AD1 schema migration to `projects` table | ⚠ **DB MIGRATION NEEDED**: `Project` ORM model in `db/models/projects.py` does NOT have `pixel_size_um` column yet. Plan must include alembic migration. Also: `Image.pixel_size_um` (`upload.py:129`) and `UploadItem.pixel_size_um` (`upload.py:193`) already exist — clarify whether these stay (image-level override) or are removed |
| `max_images` | NOT in plan rename list | ✅ stays as-is |
| `workers` (pipeline) | NOT in plan rename list | ✅ stays as-is |
| `quality` → `thumbnail_quality` | Phase 1 (Thumbnails schema rename + AliasChoices) | ⚠ `tests/api/test_errors.py:14`, `:19` use `field="quality"` as example — update to new name; `web/src/hooks/__tests__/useStepProgress.test.ts:57` test fixture |

### Critical gaps for PM to resolve before Phase 1 starts

1. **`gaussian_sigma`** — audit list includes it but plan brief's rename list doesn't mention it. Confirm rename target or remove from refactor scope.
2. **`pixel_size_um` AD1 schema migration** — `projects.pixel_size_um` column does not exist. Plan must add an alembic step. Also clarify relationship with existing `images.pixel_size_um` and `upload_items.pixel_size_um` (override hierarchy?).
3. **`raw_ext` manifest resolution** — wrapper layer needs a manifest reader in scope. Where does the wrapper get the manifest? Through DI / through scan_id lookup?
4. **Metrics dict embed sites** — `run_pipeline.py:418` (`raw_ext`), `run.py:559` / `run_pipeline.py:441` (`r_max_px`), `run_pipeline.py:417` (`repr_mode`), and several others embed renamed fields into a `metrics` dict surfaced via SSE / persisted to `runs.metrics` JSONB. Plan must specify whether embed key follows new name (breaking for analytics consumers) or keeps old name (drift between body and metrics).
5. **`worker/tasks.py:100` `weights_path`** — confirm rename propagates into RQ task signature, or worker boundary stays on legacy name.

## Migration risk notes

- **Stored JSONB blobs in `analyses.proximity_params` / `analyses.background_params`** referencing old field names: cannot count rows from this audit (read-only filesystem only). PM/devops to query non-prod DB. Code paths that populate these:
  - `analyses.amg_params` / `proximity_params` / `background_params` are written via `runs` table mediation in `api/services/analyses.py`
  - `runs.metrics` JSONB is populated by route handlers (`run.py:165`, `:258`, `:559`; `run_pipeline.py:313`, `:418`, `:442`)
  - Any backfill script must be additive (write new keys alongside old) until Phase 5 alias removal
- **Parity tests** (`tests/parity/*`) are golden-data driven: `regenerate_golden.py` rebuilds fixtures from `link_distance_um=1.0, pixel_size_um=0.5, workers=1`. After rename, golden files must be regenerated AND the regeneration script must use new names, OR the regenerator stays on legacy aliases for one cycle.
- **External scripts/cronjobs**: searched `scripts/`, `deploy/`, `.github/` — no direct references to the renamed fields. Deploy uses uvicorn without `--workers`; no automation passes pipeline params at runtime in repo.
- **Frontend**: `web/src/components/StepCard.tsx:12` sends `params={}`. No migration needed for v1 of the SPA. Phase 3 (frontend forms) is greenfield: implement directly with new names.

## Next steps

Phase 1 (api-developer) starts at Task 1.1 with this audit pinned to context. Recommended Phase 1 order:
1. Resolve the 5 gaps above with PM (one batched question).
2. Update `src/flake_analysis/api/schemas/compute.py` with new names + `AliasChoices` legacy aliases (per AD3).
3. Update `src/flake_analysis/api/routes/run.py` and `run_pipeline.py` body field accesses.
4. Update wrapper layer (`src/flake_analysis/pipeline/*.py`) to translate new schema names → existing core kwargs (per AD2).
5. Update tests in dependency order: `tests/api/test_compute_schemas.py` first (validates schema), then route SSE tests (`tests/api/test_run_*_sse.py`), then composite (`tests/api/test_run_pipeline_sse.py`).
6. Defer `pixel_size_um` AD1 migration to a parallel db-specialist task (Phase 1.5 or Phase 2).
7. SAM `weights_path → model` + S3 resolver: separate Phase 4 task pinned to AD4.
