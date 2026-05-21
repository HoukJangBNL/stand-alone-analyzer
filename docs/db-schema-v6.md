# Qpress SAM Pipeline — DB Schema v6

> **Status**: Final. Source of truth for the initial alembic migration.
>
> **Stack**: PostgreSQL on RDS (`db.t4g.small`) + SQLAlchemy 2.x async + asyncpg + alembic.
>
> **Scope**: 2D-materials microscopy SAM inference pipeline. Multiple LoRA models compared on the same scan data.
> Pipeline: upload → background → SAM (GPU) → domain_stats → domain_proximity (flake formation) → user
> branches: `domain_analyses` (selector + clustering + labels) and `flake_analyses` (curation).
>
> **Scale target**: ~100K images/month, ~100 SAM masks (domains) per image.

---

## 1. Changes from v5

- **`samples` 테이블 삭제.** `analysis × image` 컨테이너는 더 이상 모델링하지 않음. `domains`/`flakes`는 `analysis_id`(+`image_id`) 직접 참조.
- **Cross-image flake 준비.** `flakes`는 더 이상 단일 image에 묶이지 않음. `coordinate_system` 컬럼으로 `image_px`(현재) vs `stage_um`(미래) 구분.
- **Branch 아키텍처 도입.** 사용자가 분석 결과 위에서 두 갈래로 작업:
  - `domain_analyses` = selector(필터) + clustering + labeling 한 묶음
  - `flake_analyses` = explorer 필터 + flake 단위 큐레이션
  - Branch끼리 cross-link: `flake_analyses.domain_analysis_id` (선택적)
- **`users` 테이블 신설.** 모든 `*_by` 컬럼 → `created_by_id BIGINT REFERENCES users(id)`. 시스템 워커는 `('system')` 행 사용.
- **ENUM 타입 도입.** `upload_session_status`, `upload_item_status`, `pipeline_status`. `pipeline_step`은 TEXT + CHECK로 유지 (확장 용이).
- **Status 값 통일.** `done` → `completed` 전부 교체.
- **Pixel size canonicalization.** `analyses.pixel_size_um` 제거 → `images.pixel_size_um`로 이동 (이미지 단위 기록).
- **Linking distance canonicalization.** `analyses.link_distance_um` 제거. `link_distance_px NOT NULL` 만 유지 (proximity는 px 기준 계산).
- **Promote area thresholds.** `min_area_px`, `max_area_px`를 JSONB → 컬럼으로 승격 (인덱스/필터링 용이).
- **Background per-analysis ref.** `analyses.background_s3_uri` 추가 (분석당 1개 reference 이미지). 과거 `bg_*`는 모두 `background_*`로 일관 명명.
- **Naming hygiene.** `analyses.label` → `name`. `runs.spot` → `is_spot`. `runs.finished_at` → `completed_at`. `images.uploaded_at` → `created_at` (+`updated_at` 추가). `flake_curations.curated_by/at` → `created_by_id/at` (+`updated_at`).
- **Step rename.** 파이프라인 4번째 step `proximity` → `domain_proximity` (도메인 용어 명확화).
- **Selector/explorer rename.** `domain_analyses.filter_params` → `selector_params`. `flake_analyses.filter_params` → `explorer_params`.
- **Curation tag rename.** `flake_curations.label` → `tag` (`domain_groups.label`과 혼동 방지).
- **`analyses.status` GENERATED column.** `steps_done` JSONB에서 자동 도출 (이전: 워커 수동 갱신).
- **Param hashes.** `domain_analyses.selector_params_hash`, `clustering_params_hash` 추가 (재계산 skip 판단용).
- **Cross-analysis FK 정합.** `domain_assignments`에 composite FK로 `analysis_id` 동일성 강제.
- **`domains.flake_id ON DELETE SET NULL`** — proximity 재실행 시 flake 삭제로 인한 cascade 폭발 방지.
- **`samples.n_flakes` / `samples.n_domains` 카운터 제거** (samples 자체 제거에 따라 자연 소멸). 필요 시 group-by로 도출.

---

## 2. Entity-Relationship Diagram

```
┌──────────────┐
│    users     │
│──────────────│
│ id (PK)      │
│ username UQ  │
│ created_at   │
└──────┬───────┘
       │ created_by_id (모든 *_by FK)
       │
       ▼ N
┌──────────────────────────────────────────────────────────────────┐
│ Scan & Upload Domain                                             │
│                                                                  │
│  ┌──────────────┐         ┌──────────────────┐                   │
│  │   models     │         │ upload_sessions  │                   │
│  │──────────────│         │──────────────────│                   │
│  │ id (PK)      │         │ id (PK)          │                   │
│  │ name UQ      │         │ scan_id (FK)     │◄──┐               │
│  │ base_model   │         │ total_files      │   │               │
│  │ s3_uri       │         │ completed_files  │   │               │
│  │ description  │         │ failed_files     │   │               │
│  │ created_at   │         │ status (ENUM)    │   │               │
│  └──────┬───────┘         │ manifest_s3_uri  │   │               │
│         │                 │ created_at       │   │               │
│         │                 │ updated_at       │   │               │
│         │                 │ created_by_id    │   │               │
│         │                 └────────┬─────────┘   │               │
│         │                          │ 1           │               │
│         │                          ▼ N           │               │
│         │                 ┌──────────────────┐   │               │
│         │                 │ upload_items     │   │               │
│         │                 │──────────────────│   │               │
│         │                 │ id (PK)          │   │               │
│         │                 │ session_id (FK)  │   │               │
│         │                 │ sha256           │   │               │
│         │                 │ filename         │   │               │
│         │                 │ size_bytes       │   │               │
│         │                 │ status (ENUM)    │   │               │
│         │                 │ s3_uri           │   │               │
│         │                 │ error            │   │               │
│         │                 │ attempts         │   │               │
│         │                 │ image_id (FK)    │──┐│               │
│         │                 │ grid_ix/iy       │  ││               │
│         │                 │ stage_x/y_um     │  ││               │
│         │                 │ pixel_size_um    │  ││               │
│         │                 │ created_at       │  ││               │
│         │                 │ started_at       │  ││               │
│         │                 │ completed_at     │  ││               │
│         │                 └──────────────────┘  ││               │
│         │                                       ││               │
│         │                ┌──────────────┐       ││               │
│         │                │    scans     │◄──────┘│               │
│         │                │──────────────│        │               │
│         │                │ id (PK)      │◄───────┘               │
│         │                │ name         │                        │
│         │                │ material     │                        │
│         │                │ description  │                        │
│         │                │ image_count  │ (cached)               │
│         │                │ created_at   │                        │
│         │                │ updated_at   │                        │
│         │                │ created_by_id│                        │
│         │                └──────┬───────┘                        │
│         │                       │                                │
│         │                       ├──────────────┐                 │
│         │                       ▼ N            ▼ N               │
│         │              ┌──────────────┐  ┌──────────────┐        │
│         │              │   images     │  │  analyses    │        │
│         │              │──────────────│  │──────────────│        │
│         │              │ id (PK)      │  │ id (PK)      │        │
│         │              │ scan_id (FK) │  │ scan_id (FK) │        │
│         │              │ sha256       │  │ model_id (FK)│◄───────┘
│         │              │ s3_uri       │  │ name         │
│         │              │ width/height │  │ amg_params   │
│         │              │ filename     │  │ background_  │
│         │              │ grid_ix/iy   │  │   params     │
│         │              │ stage_x/y_um │  │ background_  │
│         │              │ pixel_size_um│  │   s3_uri     │
│         │              │ created_at   │  │ link_dist_px │
│         │              │ updated_at   │  │ min_area_px  │
│         │              │ UNIQUE(scan, │  │ max_area_px  │
│         │              │   sha256)    │  │ proximity_p. │
│         │              └──────┬───────┘  │ steps_done   │
│         │                     │          │ status (GEN) │
│         │                     │          │ notes        │
│         │                     │          │ created_at   │
│         │                     │          │ updated_at   │
│         │                     │          │ created_by_id│
│         │                     │          └──────┬───────┘
│         │                     │                 │
│         │                     │                 ├──────┬─────────┐
│         │                     │                 ▼ N    ▼ N       ▼ N (branches)
│         │                     │           ┌──────┐ ┌────────┐  (see §below)
│         │                     │           │ runs │ │ flakes │
│         │                     │           │──────│ │────────│
│         │                     │           │ id   │ │ id     │
│         │                     │           │ anal │ │ anal_id│
│         │                     │           │ step │ │ coord_ │
│         │                     │           │ stat │ │   sys  │
│         │                     │           │ inst │ │ anchor_│
│         │                     │           │ is_  │ │   img  │
│         │                     │           │  spot│ │ n_doms │
│         │                     │           │ start│ │ bbox   │
│         │                     │           │ compl│ │ area   │
│         │                     │           │ error│ │ rle    │
│         │                     │           │ metr │ │ created│
│         │                     │           └──────┘ └───┬────┘
│         │                     │                        │ 1
│         │                     │              ┌─────────┘
│         │                     │              ▼ N
│         │                     │      ┌────────────┐
│         │                     └─────►│  domains   │
│         │                            │────────────│
│         │                            │ id (PK)    │
│         │                            │ analysis_id│
│         │                            │ image_id   │
│         │                            │ flake_id   │ (SET NULL)
│         │                            │ bbox       │
│         │                            │ area       │
│         │                            │ rle        │
│         │                            │ sam_score  │
│         │                            │ stats      │
│         │                            │ created_at │
│         │                            └─────┬──────┘
└─────────┴─────────────────────────────────┴─────────────────────┘
                                            │
        ┌───────────────────────────────────┼─────────────────────────────────┐
        │ Domain Analysis Branch            │   Flake Analysis Branch         │
        │                                   │                                 │
        │  ┌────────────────────┐           │   ┌────────────────────┐        │
        │  │ domain_analyses    │           │   │ flake_analyses     │        │
        │  │────────────────────│           │   │────────────────────│        │
        │  │ id (PK)            │           │   │ id (PK)            │        │
        │  │ analysis_id (FK)   │           │   │ analysis_id (FK)   │        │
        │  │ name               │           │   │ name               │        │
        │  │ selector_params    │           │   │ domain_analysis_id ├──── (cross-link)
        │  │ selector_params_h. │           │   │   (FK SET NULL)    │        │
        │  │ n_selected_domains │           │   │ explorer_params    │        │
        │  │ method             │           │   │ notes              │        │
        │  │ clustering_params  │           │   │ created_at/updated │        │
        │  │ clustering_params_h│           │   │ created_by_id      │        │
        │  │ model_s3_uri       │           │   │ UNIQUE(anal,name)  │        │
        │  │ status (ENUM)      │           │   └─────────┬──────────┘        │
        │  │ created_at/updated │           │             │ 1                 │
        │  │ created_by_id      │           │             ▼ N                 │
        │  │ UNIQUE(anal,name)  │           │   ┌────────────────────┐        │
        │  └─────────┬──────────┘           │   │ flake_curations    │        │
        │            │ 1                    │   │────────────────────│        │
        │            ├───────────┐          │   │ id (PK)            │        │
        │            ▼ N         ▼ N        │   │ flake_analysis_id  │        │
        │  ┌──────────────┐ ┌──────────────┐│   │ flake_id (FK)      │        │
        │  │ domain_groups│ │ domain_      ││   │ tag                │        │
        │  │──────────────│ │ assignments  ││   │ is_of_interest     │        │
        │  │ id (PK)      │ │──────────────││   │ notes              │        │
        │  │ domain_anal_ │ │ analysis_id  ││   │ created_at/updated │        │
        │  │   id (FK)    │ │ domain_anal_ ││   │ created_by_id      │        │
        │  │ cluster_id   │ │   id (FK)    ││   │ UNIQUE(fa, flake)  │        │
        │  │ label        │ │ domain_id    ││   └────────────────────┘        │
        │  │ color        │ │   (FK)       ││                                 │
        │  │ UNIQUE(da,   │ │ domain_group_││                                 │
        │  │   cluster)   │ │   id (FK)    ││                                 │
        │  └──────┬───────┘ │ posterior    ││                                 │
        │         │         │ created_at   ││                                 │
        │         └────────►│ PK(da_id,    ││                                 │
        │                   │   domain_id) ││                                 │
        │                   └──────────────┘│                                 │
        └───────────────────────────────────┴─────────────────────────────────┘
```

Composite FK on `domain_assignments`:
- `(analysis_id, domain_id) → domains(analysis_id, id)` — guarantees the domain belongs to the same parent analysis.
- `(analysis_id, domain_analysis_id) → domain_analyses(analysis_id, id)` — guarantees the domain_analysis belongs to the same parent analysis.

---

## 3. Cardinality Summary

```
users           1 ─────< N  (any *_by FK)
scans           1 ─────< N  upload_sessions
upload_sessions 1 ─────< N  upload_items
upload_items    0 ─────< 1  images           (성공한 항목만 image 행과 연결)
scans           1 ─────< N  images
scans           1 ─────< N  analyses
models          1 ─────< N  analyses
analyses        1 ─────< N  runs
analyses        1 ─────< N  domains
analyses        1 ─────< N  flakes
images          1 ─────< N  domains
flakes          1 ─────< N  domains          (ON DELETE SET NULL)

analyses        1 ─────< N  domain_analyses
domain_analyses 1 ─────< N  domain_groups
domain_analyses 1 ─────< N  domain_assignments
domains         1 ─────< N  domain_assignments  (across N domain_analyses)
domain_groups   1 ─────< N  domain_assignments

analyses        1 ─────< N  flake_analyses
domain_analyses 0 ─────< N  flake_analyses    (optional cross-link)
flake_analyses  1 ─────< N  flake_curations
flakes          1 ─────< N  flake_curations   (across N flake_analyses)
```

---

## 4. End-to-End Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│ 0. Scan 생성: POST /scans (name, material)                   │
│    → scans 1행 (created_by_id = users.system)                │
└────────────────┬─────────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. 업로드 (resilient + manifest 기반, v5와 동일 워크플로우)  │
│    upload_sessions 1행, upload_items N행, images M행 (M ≤ N) │
│    images.pixel_size_um 채워짐 (manifest 또는 TIFF)          │
└────────────────┬─────────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. 분석 시작                                                 │
│    POST /analyses (scan_id, model_id, params)                │
│    → analyses 1행 (steps_done={}, status=GENERATED 'pending')│
│    ※ samples 행 생성 없음 (테이블 자체가 없음)               │
└────────────────┬─────────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Background 단계 (CPU/Lambda) — 분석당 1개 ref 이미지      │
│    runs +1 (step='background')                               │
│    analyses.background_s3_uri 채움                           │
│    steps_done.background = true → status='running'           │
└────────────────┬─────────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. SAM 단계 (GPU spot, 예: g6e.48xlarge)                     │
│    runs +1 (step='sam', instance_type, is_spot, started_at..)│
│    domains N행/이미지 (RLE, bbox, sam_score, image_id)       │
│    Per-image 실패는 runs.metrics.per_image_failures JSONB 에  │
│    누적 (image_id → {error}). 성공 카운트는 domains GROUP BY.│
│    steps_done.sam = true                                     │
└────────────────┬─────────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. Domain stats (CPU)                                        │
│    Worker reads stats.npz from S3, decomposes per domain,    │
│    UPDATE domains SET stats = jsonb (mean_intensity, ...)    │
│    NPZ 파일은 DB에 들어가지 않음 (S3 artifact만 보존)        │
│    steps_done.domain_stats = true                            │
└────────────────┬─────────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. Domain Proximity + Flake 형성 (CPU, parallel)             │
│    distances.parquet → S3 only (DB 미저장)                   │
│    flake_assignments.parquet → flakes 행 + domains.flake_id  │
│    flakes.coordinate_system='image_px',                      │
│    flakes.anchor_image_id = (singleton/multi-domain 의 기준) │
│    flakes.segmentation_rle = merged RLE                      │
│    steps_done.domain_proximity = true → status='completed'   │
└────────────────┬─────────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────────┐
│ 7. 사용자 분석 (GUI, 두 갈래)                                │
│  ▸ Domain branch: domain_analyses (selector + clustering 한  │
│    묶음 commit). domain_groups + domain_assignments 일괄 INS.│
│    Selector-alone export 는 ephemeral (DB 저장 X).           │
│  ▸ Flake branch: flake_analyses + flake_curations.           │
│    flake_analyses.domain_analysis_id 로 cross-link 가능.     │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. Tables at a Glance

| Table | Role | Filled by |
|---|---|---|
| `users` | 사용자/시스템 식별 (FK 대상) | 사람 (또는 seed `'system'`) |
| `models` | LoRA 체크포인트 메타 | 사람 (모델 등록 시) |
| `scans` | 업로드 묶음 (= 실험 단위) | API (스캔 생성 시) |
| `upload_sessions` | 업로드 배치 단위 + 진행률 | API (init 시) |
| `upload_items` | 파일 단위 업로드 상태/재시도 | API (+ S3 이벤트 옵션) |
| `images` | 성공한 이미지 + S3 위치 + 격자/스테이지 좌표 + pixel_size_um | API (complete 시) |
| `analyses` | (scan, model, params) 분석 단위, status는 GENERATED | API + 워커 |
| `runs` | 단계별 실행 시도 audit (재시도 포함, per-image 실패는 metrics JSONB) | 워커 |
| `flakes` | 분석 단위 flake 집합 (cross-image 대비 coord_sys 컬럼 보유) | CPU 워커 |
| `domains` | SAM mask 1개 = 1행, RLE 보존, flake_id N:1 | GPU/CPU 워커 |
| `domain_analyses` | 사용자 정의 selector + clustering 결과 묶음 | 사람 (GUI) |
| `domain_groups` | 클러스터 정의 (label, color) | 사람 (GUI) |
| `domain_assignments` | domain ↔ group 매핑 (per domain_analysis) | 사람 (GUI) |
| `flake_analyses` | 사용자 explorer 세션 (domain_analyses 와 옵션 cross-link) | 사람 (GUI) |
| `flake_curations` | flake 단위 큐레이션 (tag, of-interest, notes) | 사람 (GUI) |

Total: **15 tables**.

---

## 6. Key Design Decisions

```
✅ samples 제거 → analyses + image_id 직접 참조
   • analysis × image 컨테이너는 비즈니스 가치 없음
   • cross-image flake 도입 시 image 단위 컨테이너가 오히려 방해

✅ flakes는 analysis 단위 (cross-image 가능)
   • coordinate_system 으로 image_px / stage_um 구분
   • 현 파이프라인은 image_px + anchor_image_id 만 사용
   • stage_um 은 미래 cross-image stitching 용 reservation

✅ Branch 아키텍처 (domain / flake)
   • domain_analyses = selector + clustering + labeling 한 단위 commit
   • flake_analyses = explorer + curation 한 단위
   • cross-link: flake_analyses.domain_analysis_id (선택적, SET NULL)

✅ users 테이블 도입 + 'system' seed
   • 모든 *_by → created_by_id BIGINT REFERENCES users(id)
   • 인증 미도입 단계에는 'system' 행 사용
   • 추후 인증 추가 시 username UNIQUE 만으로 매핑 가능

✅ ENUM vs TEXT+CHECK 절충
   • upload_session_status / upload_item_status / pipeline_status
     → ENUM (closed set, 거의 변경 X)
   • pipeline_step → TEXT + CHECK
     (qc, training 등 향후 step 추가 시 ALTER TYPE 회피)

✅ analyses.status = GENERATED column
   • steps_done JSONB 에서 자동 도출
   • 워커가 두 컬럼을 동기화할 필요 없음 → drift 위험 제거
   • Postgres CASE/jsonb operators 모두 IMMUTABLE → STORED 가능

✅ pixel_size_um은 images 캐노니컬
   • analyses.pixel_size_um 제거 (이미지마다 다를 수 있음)
   • proximity 단계는 link_distance_px 를 이미지별 변환 없이 사용

✅ Composite FK로 cross-analysis 정합 강제
   • domain_assignments(analysis_id, domain_id, domain_analysis_id)
     → domains/domain_analyses 의 (analysis_id, id) 와 매칭
   • 한 assignment 안에서 두 부모가 다른 analysis 일 가능성 차단

✅ ON DELETE SET NULL on domains.flake_id
   • Proximity 재실행 시 flakes 만 DELETE → domains 는 살아있음
   • DELETE CASCADE 였다면 SAM 결과까지 날아감 (재계산 비용 큼)

✅ Storage 분리 유지
   • DB: 메타 + RLE + stats + 라벨/큐레이션
   • S3: 원본, background, distances.parquet,
         flake_assignments.parquet, gmm_model.pkl, etc.

✅ Param hash 기반 재계산 skip
   • domain_analyses.selector_params_hash / clustering_params_hash
   • clustering 만 바뀌면 selector 결과 재사용 (워커 책임)
```

---

## 7. SQL DDL

```sql
-- =============================================================
-- ENUMs
-- =============================================================

CREATE TYPE upload_session_status AS ENUM ('active', 'completed', 'aborted');
CREATE TYPE upload_item_status    AS ENUM ('pending', 'uploading', 'uploaded', 'failed');
CREATE TYPE pipeline_status       AS ENUM ('pending', 'running', 'completed', 'failed');
-- pipeline_step 은 TEXT + CHECK 로 처리 (ALTER TYPE 회피)

-- =============================================================
-- 1) Users (FK target for all *_by columns)
-- =============================================================

CREATE TABLE users (
    id         BIGSERIAL PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed system user (used by workers and pre-auth API calls)
INSERT INTO users (username) VALUES ('system');

-- =============================================================
-- 2) Models — LoRA checkpoints
-- =============================================================

CREATE TABLE models (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    base_model  TEXT NOT NULL,
    s3_uri      TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================
-- 3) Scans — user upload batches (= experiment units)
-- =============================================================

CREATE TABLE scans (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    material      TEXT,
    description   TEXT,
    image_count   INT NOT NULL DEFAULT 0,           -- app-maintained cache
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_id BIGINT REFERENCES users(id)
);
CREATE INDEX scans_material_idx ON scans(material) WHERE material IS NOT NULL;

-- =============================================================
-- 4) Upload Sessions — batch tracking with progress
-- =============================================================

CREATE TABLE upload_sessions (
    id              BIGSERIAL PRIMARY KEY,
    scan_id         BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    total_files     INT NOT NULL,
    completed_files INT NOT NULL DEFAULT 0,
    failed_files    INT NOT NULL DEFAULT 0,
    status          upload_session_status NOT NULL DEFAULT 'active',
    manifest_s3_uri TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_id   BIGINT REFERENCES users(id)
);
CREATE INDEX upload_sessions_scan_idx ON upload_sessions(scan_id);

-- =============================================================
-- 5) Images — successfully uploaded images
--    NOTE: must exist before upload_items (FK)
-- =============================================================

CREATE TABLE images (
    id            BIGSERIAL PRIMARY KEY,
    scan_id       BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    sha256        CHAR(64) NOT NULL,
    s3_uri        TEXT NOT NULL,
    width         INT NOT NULL,
    height        INT NOT NULL,
    filename      TEXT,
    grid_ix       INT,
    grid_iy       INT,
    stage_x_um    REAL,
    stage_y_um    REAL,
    pixel_size_um REAL,                              -- canonical (manifest or TIFF). NULLable.
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(scan_id, sha256)
);
CREATE INDEX images_scan_idx ON images(scan_id);
CREATE INDEX images_grid_idx ON images(scan_id, grid_ix, grid_iy)
    WHERE grid_ix IS NOT NULL AND grid_iy IS NOT NULL;

-- =============================================================
-- 6) Upload Items — per-file upload state, retry, manifest fields
-- =============================================================

CREATE TABLE upload_items (
    id            BIGSERIAL PRIMARY KEY,
    session_id    BIGINT NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,
    sha256        CHAR(64) NOT NULL,
    filename      TEXT NOT NULL,
    size_bytes    BIGINT,
    status        upload_item_status NOT NULL DEFAULT 'pending',
    s3_uri        TEXT,
    error         TEXT,
    attempts      INT NOT NULL DEFAULT 0,
    image_id      BIGINT REFERENCES images(id),
    -- manifest-supplied per-image metadata (copied to images on success)
    grid_ix       INT,
    grid_iy       INT,
    stage_x_um    REAL,
    stage_y_um    REAL,
    pixel_size_um REAL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    UNIQUE(session_id, sha256)
);
CREATE INDEX upload_items_session_status_idx
    ON upload_items(session_id, status)
    WHERE status IN ('pending', 'uploading');

-- =============================================================
-- 7) Analyses — (scan, model, params) unit, status is GENERATED
-- =============================================================

CREATE TABLE analyses (
    id                BIGSERIAL PRIMARY KEY,
    scan_id           BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    model_id          BIGINT NOT NULL REFERENCES models(id),
    name              TEXT,                          -- nullable user-facing label
    amg_params        JSONB NOT NULL,
    background_params JSONB,
    background_s3_uri TEXT,                          -- single per-analysis reference image
    link_distance_px  REAL NOT NULL,                 -- canonical proximity threshold (px)
    min_area_px       INT NOT NULL DEFAULT 10,
    max_area_px       INT,                           -- NULL = no upper bound
    proximity_params  JSONB,                         -- compute knobs (r_max_px, d_touch_px, fallback_pixel_size_um, ...)
    steps_done        JSONB NOT NULL DEFAULT '{}',
    status            pipeline_status GENERATED ALWAYS AS (
        CASE
            WHEN steps_done ? 'failed'
                THEN 'failed'::pipeline_status
            WHEN steps_done ? 'domain_proximity'
                 AND (steps_done ->> 'domain_proximity')::boolean
                THEN 'completed'::pipeline_status
            WHEN jsonb_typeof(steps_done) = 'object'
                 AND steps_done <> '{}'::jsonb
                THEN 'running'::pipeline_status
            ELSE 'pending'::pipeline_status
        END
    ) STORED,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_id     BIGINT REFERENCES users(id)
);
CREATE UNIQUE INDEX analyses_scan_model_name_uniq
    ON analyses(scan_id, model_id, name)
    WHERE name IS NOT NULL;
CREATE INDEX analyses_scan_idx ON analyses(scan_id);

-- =============================================================
-- 8) Runs — per-step execution attempts (audit log)
-- =============================================================

CREATE TABLE runs (
    id            BIGSERIAL PRIMARY KEY,
    analysis_id   BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    step          TEXT NOT NULL CHECK (step IN (
                      'background',
                      'sam',
                      'domain_stats',
                      'domain_proximity'
                  )),
    status        pipeline_status NOT NULL,
    instance_type TEXT,
    instance_id   TEXT,
    is_spot       BOOLEAN,
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    error         TEXT,
    metrics       JSONB,                              -- includes per_image_failures map
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX runs_analysis_idx      ON runs(analysis_id);
CREATE INDEX runs_analysis_step_idx ON runs(analysis_id, step);

-- =============================================================
-- 9) Flakes — analysis-scoped flake set (cross-image-ready)
--    NOTE: must exist before domains (FK)
-- =============================================================

CREATE TABLE flakes (
    id                BIGSERIAL PRIMARY KEY,
    analysis_id       BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    coordinate_system TEXT NOT NULL DEFAULT 'image_px'
                      CHECK (coordinate_system IN ('image_px', 'stage_um')),
    anchor_image_id   BIGINT REFERENCES images(id),
    -- anchor_image_id is REQUIRED when coordinate_system='image_px',
    -- NULL when 'stage_um'. Enforced in application layer.
    n_domains         INT NOT NULL,
    bbox              INT[] NOT NULL,
    area              INT NOT NULL,
    segmentation_rle  JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX flakes_analysis_idx ON flakes(analysis_id);

-- NOTE: domains and domain_analyses below carry a composite UNIQUE
--       on (analysis_id, id). domain_assignments uses those tuples
--       as composite FKs to guarantee that the assigned domain and
--       the parent domain_analysis belong to the same analysis.

-- =============================================================
-- 10) Domains — SAM masks (one row per mask)
-- =============================================================

CREATE TABLE domains (
    id               BIGSERIAL PRIMARY KEY,
    analysis_id      BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    image_id         BIGINT NOT NULL REFERENCES images(id),
    flake_id         BIGINT REFERENCES flakes(id) ON DELETE SET NULL,
    bbox             INT[] NOT NULL,
    area             INT NOT NULL,
    segmentation_rle JSONB NOT NULL,
    sam_score        REAL,
    stats            JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Composite UNIQUE so domain_assignments can FK on (analysis_id, id)
    UNIQUE (analysis_id, id)
);
CREATE INDEX domains_analysis_image_idx ON domains(analysis_id, image_id);
CREATE INDEX domains_image_idx          ON domains(image_id);
CREATE INDEX domains_flake_idx          ON domains(flake_id) WHERE flake_id IS NOT NULL;

-- =============================================================
-- 11) Domain Analyses — selector + clustering committed as one
-- =============================================================

CREATE TABLE domain_analyses (
    id                     BIGSERIAL PRIMARY KEY,
    analysis_id            BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    selector_params        JSONB NOT NULL DEFAULT '{}',
    selector_params_hash   TEXT,                       -- for reuse-skip decisions
    n_selected_domains     INT,
    method                 TEXT NOT NULL,              -- 'gmm' | 'kmeans' | ...
    clustering_params      JSONB NOT NULL,
    clustering_params_hash TEXT,
    model_s3_uri           TEXT,                       -- gmm_model.pkl, etc.
    status                 pipeline_status NOT NULL DEFAULT 'pending',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_id          BIGINT REFERENCES users(id),
    UNIQUE(analysis_id, name),
    -- Composite UNIQUE so domain_assignments can FK on (analysis_id, id)
    UNIQUE(analysis_id, id)
);

CREATE TABLE domain_groups (
    id                 BIGSERIAL PRIMARY KEY,
    domain_analysis_id BIGINT NOT NULL REFERENCES domain_analyses(id) ON DELETE CASCADE,
    cluster_id         INT NOT NULL,
    label              TEXT NOT NULL,                  -- e.g. 'group_0', '1L', 'noise'
    color              TEXT,
    UNIQUE(domain_analysis_id, cluster_id)
);

CREATE TABLE domain_assignments (
    analysis_id        BIGINT NOT NULL,
    domain_analysis_id BIGINT NOT NULL,
    domain_id          BIGINT NOT NULL,
    domain_group_id    BIGINT NOT NULL REFERENCES domain_groups(id) ON DELETE CASCADE,
    posterior          REAL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (domain_analysis_id, domain_id),
    -- Composite FKs guarantee that the domain and the domain_analysis
    -- both belong to the same parent analysis.
    FOREIGN KEY (analysis_id, domain_id)
        REFERENCES domains(analysis_id, id) ON DELETE CASCADE,
    FOREIGN KEY (analysis_id, domain_analysis_id)
        REFERENCES domain_analyses(analysis_id, id) ON DELETE CASCADE
);
CREATE INDEX domain_assignments_group_idx ON domain_assignments(domain_group_id);

-- =============================================================
-- 12) Flake Analyses — explorer session (optional cross-link to a domain_analysis)
-- =============================================================

CREATE TABLE flake_analyses (
    id                 BIGSERIAL PRIMARY KEY,
    analysis_id        BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    domain_analysis_id BIGINT REFERENCES domain_analyses(id) ON DELETE SET NULL,
    explorer_params    JSONB,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_id      BIGINT REFERENCES users(id),
    UNIQUE(analysis_id, name)
);

CREATE TABLE flake_curations (
    id                BIGSERIAL PRIMARY KEY,
    flake_analysis_id BIGINT NOT NULL REFERENCES flake_analyses(id) ON DELETE CASCADE,
    flake_id          BIGINT NOT NULL REFERENCES flakes(id) ON DELETE CASCADE,
    tag               TEXT,                            -- renamed from `label` to disambiguate from domain_groups.label
    is_of_interest    BOOLEAN NOT NULL DEFAULT FALSE,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_id     BIGINT REFERENCES users(id),
    UNIQUE(flake_analysis_id, flake_id)
);
CREATE INDEX flake_curations_flake_idx ON flake_curations(flake_id);
```

---

## 8. Conventions

These are worker/app-layer invariants. The DB does not enforce them; future contributors must respect them.

1. **`images.pixel_size_um` may be NULL** for legacy or pre-manifest uploads. The `domain_proximity` step MUST use a documented fallback (e.g., `analyses.proximity_params.fallback_pixel_size_um`) or fail loudly with a clear error. NEVER silently substitute a hard-coded constant (e.g., `0.5`).

2. **`stats.npz` is decomposed into `domains.stats JSONB` rows at DB-write time.** The NPZ file itself is not stored in DB; only the per-domain JSON view of it is. The COCO-style `annotations.json` produced by SAM is the source for `domains` rows; that file is also S3-only.

3. **`distances.parquet` is NOT stored in DB.** Only `flake_assignments.parquet` flows into `flakes` rows + `domains.flake_id` updates. Pair distances stay as S3 artifacts referenced through `runs.metrics`.

4. **Background re-run cascade** is the worker's responsibility. When the worker re-runs the `background` step on an existing analysis, in the same DB transaction it MUST:
   - Clear `steps_done.sam`, `steps_done.domain_stats`, `steps_done.domain_proximity`.
   - DELETE dependent `domains` and `flakes` rows for that `analysis_id`.
   - Leave `domain_analyses` and `flake_analyses` (user curation) intact until the user explicitly invalidates them.

   The DB does NOT enforce this cascade. `domains.flake_id ON DELETE SET NULL` exists only to prevent accidental cascade through the flake side.

5. **Derived caches are app-maintained.** `analyses.steps_done`, `flakes.n_domains`, `scans.image_count` are written by the app on the same transaction as the underlying mutation. There are no DB triggers. A nightly reconciliation job logs drift but never auto-corrects.

6. **Per-image SAM observability.** Per-image failures are recorded inside `runs.metrics` JSONB under the conventional key:
   ```json
   { "per_image_failures": { "<image_id>": { "error": "..." } } }
   ```
   Per-image success counts are derived on demand:
   ```sql
   SELECT image_id, COUNT(*) FROM domains
   WHERE analysis_id = $1 GROUP BY image_id;
   ```

7. **Selector → clustering bundling.** `domain_analyses` represents the combined selector + clustering + labeling workflow as a single commit. Selector-alone export endpoints (e.g., `/selector/export`) are ephemeral and MUST NOT persist to `domain_analyses`. Use param hashes (`selector_params_hash`) to skip selector re-computation when only clustering parameters change.

8. **`flakes.coordinate_system`.** Today's pipeline only produces `'image_px'` flakes (every flake fits inside a single image, with `anchor_image_id` set). `'stage_um'` is reserved for future cross-image stitching. Workers MUST set `anchor_image_id` whenever they write a row with `coordinate_system='image_px'`. The DB CHECK only restricts the value set; the NULL-vs-NOT-NULL rule for `anchor_image_id` lives in the worker.

9. **`pipeline_step` is TEXT + CHECK, not ENUM.** Extending the set (e.g., adding `'qc'` or `'training'`) requires updating the CHECK constraint via an alembic migration, but no `ALTER TYPE` is needed. Step values use full domain terms (`domain_proximity`, not `proximity`); keep this consistent across DB rows, log lines, and API responses.

---

## 9. Open Items

These were considered during v6 design but deferred or flagged for a future iteration. None of them block the alembic migration.

1. **GENERATED status column — final compile check.** The CASE expression in `analyses.status` uses `?`, `->>`, `jsonb_typeof`, and ENUM casts. All of these are IMMUTABLE in PostgreSQL 15+, so `GENERATED ALWAYS AS ... STORED` is expected to compile cleanly. If alembic migration generation surfaces an unexpected immutability error, fall back to a normal `pipeline_status` column updated by the worker, and add a TODO to revisit. Document the choice in the migration's docstring.

2. **`users` FK enforcement.** Today every `created_by_id` is nullable and most rows will point to the seeded `'system'` user. When real authentication lands, decide whether to (a) tighten `created_by_id` to `NOT NULL`, (b) add `auth_provider`, `email`, `display_name` columns, or (c) introduce a separate `auth_identities` table linking external IDs to `users.id`.

3. **Cross-image flake activation (`coordinate_system='stage_um'`).** Schema is ready, worker is not. When activating, a separate stitching pass will need to (a) compute global pixel→stage transforms, (b) decide whether RLE stays per-image or is rebuilt against a global canvas, (c) extend `flakes` with optional `stage_bbox_um REAL[]`. Defer until a multi-image flake demand surfaces.

4. **`flakes.bbox` / `area` units.** Currently always image-pixel units. If `stage_um` flakes land, either reinterpret these columns based on `coordinate_system` or add parallel `bbox_um`/`area_um2` columns. Decide at the time of activation, not now.

5. **Reconciliation job scope.** Drift detection (Convention #5) exists only on paper. A concrete checker (`scans.image_count` vs `COUNT(*) FROM images`, `flakes.n_domains` vs `COUNT(*) FROM domains WHERE flake_id=…`, `analyses.steps_done` vs `runs` row presence) needs to be implemented before scale-out, not before initial migration.

6. **Param-hash format.** `selector_params_hash` / `clustering_params_hash` are TEXT today with no enforced format. Recommend SHA-256 hex of canonical-JSON-serialized params, but settle the convention in worker code, not in DDL.
