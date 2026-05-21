# Qpress SAM Pipeline — DB Schema v5

> **Status**: Draft (pending final 3 questions). Once confirmed, this becomes the source of truth for the initial alembic migration.
>
> **Stack**: PostgreSQL on RDS (`db.t4g.small`) + SQLAlchemy 2.x async + asyncpg + alembic.
>
> **Changes since v4**:
> - **Resilient uploads**: New `upload_sessions` + `upload_items` tables (option B) to track partial / failed uploads and enable resume.
> - **Tile position**: `images` now stores `grid_ix`, `grid_iy`, `stage_x_um`, `stage_y_um` so mosaic rendering and spatial queries don't depend on filename parsing.
> - **Manifest-based ingestion** (option b): client supplies a manifest (CSV/JSON) at upload-session init; manifest provides per-image metadata (filename, sha256, grid_ix, grid_iy, stage coords if available).

---

## 1. Entity-Relationship Diagram

```
                                                       ┌──────────────────┐
                                                       │ upload_sessions  │
                                                       │──────────────────│
                                                       │ id (PK)          │
                                                       │ scan_id (FK)     │◄───┐
                                                       │ total_files      │    │
                                                       │ completed_files  │    │
                                                       │ failed_files     │    │
                                                       │ status           │    │
                                                       │ manifest_s3_uri  │    │
                                                       │ created_at       │    │
                                                       │ updated_at       │    │
                                                       │ created_by       │    │
                                                       └────────┬─────────┘    │
                                                                │ 1            │
                                                                ▼ N            │
                                                       ┌──────────────────┐   │
                                                       │ upload_items     │   │
                                                       │──────────────────│   │
                                                       │ id (PK)          │   │
                                                       │ session_id (FK)  │   │
                                                       │ sha256           │   │
                                                       │ filename         │   │
                                                       │ size_bytes       │   │
                                                       │ status           │   │
                                                       │ s3_uri           │   │
                                                       │ error            │   │
                                                       │ attempts         │   │
                                                       │ image_id (FK)    │──┐│
                                                       │ grid_ix          │  ││
                                                       │ grid_iy          │  ││
                                                       │ stage_x_um       │  ││
                                                       │ stage_y_um       │  ││
                                                       │ started_at       │  ││
                                                       │ completed_at     │  ││
                                                       │ UNIQUE(sess,sha) │  ││
                                                       └──────────────────┘  ││
                                                                             ││
┌──────────────┐                    ┌──────────────┐                         ││
│   models     │                    │    scans     │◄────────────────────────┘│
│──────────────│                    │──────────────│                          │
│ id (PK)      │                    │ id (PK)      │                          │
│ name UNIQUE  │                    │ name         │                          │
│ base_model   │                    │ material     │                          │
│ s3_uri       │                    │ description  │                          │
│ description  │                    │ image_count  │                          │
│ created_at   │                    │ created_at   │                          │
└──────┬───────┘                    │ created_by   │                          │
       │                            └──────┬───────┘                          │
       │                                   │                                  │
       │                                   ├──────────────────┐               │
       │                                   ▼ N                ▼ N             │
       │                          ┌──────────────┐   ┌──────────────┐         │
       │                          │   images     │◄──┤  analyses    │         │
       │                          │──────────────│   │──────────────│         │
       │                          │ id (PK)      │◄──┘ id (PK)      │         │
       │                          │ scan_id (FK) │   │ scan_id (FK) │         │
       │                          │ sha256       │   │ model_id (FK)│         │
       │                          │ s3_uri       │   │ amg_params   │         │
       │                          │ width        │   │ bg_params    │         │
       │                          │ height       │   │ proximity_p. │         │
       │                          │ filename     │   │ steps_done   │         │
       │                          │ grid_ix      │   │ status       │         │
       │                          │ grid_iy      │   │ label        │         │
       │                          │ stage_x_um   │   │ notes        │         │
       │                          │ stage_y_um   │   │ created_at   │         │
       │                          │ uploaded_at  │   │ updated_at   │         │
       │                          │              │   └──────┬───────┘         │
       │                          │ UNIQUE(scan, │          │                 │
       │                          │   sha256)    │          │ 1               │
       │                          └──────┬───────┘          ├────────────┐    │
       │                                 │                  │            │    │
       │                                 │                  ▼ N          ▼ N  │
       │                                 │           ┌──────────┐  ┌──────────┐
       │                                 │           │  runs    │  │ samples  │
       │                                 │           │──────────│  │──────────│
       │                                 │           │ id (PK)  │  │ id (PK)  │
       │                                 │           │ analysis │  │ analysis │
       │                                 │           │ step     │  │ image_id ├─┐
       │                                 │           │ status   │  │ bg_s3_uri│ │
       │                                 │           │ instance │  │ n_domains│ │
       │                                 │           │ spot     │  │ n_flakes │ │
       │                                 │           │ started  │  │ UNIQUE(  │ │
       │                                 │           │ finished │  │  anal,   │ │
       │                                 │           │ error    │  │  image)  │ │
       │                                 │           │ metrics  │  └────┬─────┘ │
       │                                 │           └──────────┘       │       │
       └─────────────────────────────────┴──────────────────────────────┴───────┘
                                                                        │ 1
                                                                        ├──────────┐
                                                                        ▼ N        ▼ N
                                                                ┌──────────┐ ┌──────────┐
                                                                │ domains  │ │ flakes   │
                                                                │──────────│ │──────────│
                                                                │ id (PK)  │ │ id (PK)  │
                                                                │ sample   │ │ sample   │
                                                                │ flake_id ├─┤ n_domains│
                                                                │ bbox     │ │ bbox     │
                                                                │ area     │ │ area     │
                                                                │ rle      │ │ rle      │
                                                                │ sam_score│ │ label    │
                                                                │ stats    │ │ reviewed │
                                                                └────┬─────┘ │ rev_notes│
                                                                     │       └──────────┘
                                                                     │ 1
                                                                     ▼ N
                                                          ┌──────────────────┐
                                                          │ domain_labels    │
                                                          │──────────────────│
                                                          │ id (PK)          │
                                                          │ domain_id (FK)   │
                                                          │ label            │
                                                          │ labeled_at       │
                                                          │ labeled_by       │
                                                          │ UNIQUE(dom,label)│
                                                          └──────────────────┘
```

---

## 2. Cardinality Summary

```
scans          1 ─────< N  upload_sessions  (재업로드/추가업로드 시 여러 개)
upload_sessions 1 ─────< N upload_items
upload_items   0 ─────< 1  images          (성공한 항목만 image 행과 연결)
scans          1 ─────< N  images          (성공한 이미지들만 들어감)
scans          1 ─────< N  analyses
models         1 ─────< N  analyses
analyses       1 ─────< N  runs
analyses       1 ─────< N  samples
images         1 ─────< N  samples
samples        1 ─────< N  domains
samples        1 ─────< N  flakes
flakes         1 ─────< N  domains
domains        1 ─────< N  domain_labels
```

---

## 3. Upload Flow (Resilient)

```
┌──────────────────────────────────────────────────────────┐
│ 1. Manifest 준비 (client)                                │
│  사용자가 업로드 디렉토리 + manifest.csv 준비             │
│  manifest.csv 컬럼:                                      │
│    filename, sha256, size_bytes,                         │
│    grid_ix, grid_iy, stage_x_um, stage_y_um              │
│  (grid/stage는 옵션, 없으면 NULL)                        │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 2. Session 시작                                          │
│  POST /scans/{id}/upload-sessions                        │
│   body: { manifest: [ {filename, sha256, size, ...} ] }  │
│  → upload_sessions 1행 (status='active', total=N)        │
│    upload_items N행 (status='pending')                   │
│    각 item에 presigned PUT URL 반환                      │
│  → manifest 원본은 S3에 저장, manifest_s3_uri 기록       │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 3. Client 직접 업로드 (parallel, S3 PUT)                 │
│  병목 없이 S3로 직행 (FastAPI 대역폭 우회)               │
│  client는 각 PUT 성공/실패를 추적                        │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 4. Completion 통보                                       │
│  POST /upload-sessions/{id}/complete                     │
│   body: { uploaded: [sha256...], failed: [{sha,err}] }   │
│  → upload_items 상태 업데이트                            │
│  → 성공한 items: images 행 생성 + image_id 연결          │
│    (manifest의 grid_ix/iy/stage 좌표를 images로 복사)    │
│  → upload_sessions.completed_files / failed_files 갱신   │
│    모두 처리되면 status='completed'                      │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 5. Resume (네트워크 끊김/브라우저 닫힘 대응)             │
│  GET /upload-sessions/{id}/pending                       │
│  → status='pending' or 'failed' items 반환               │
│  client가 해당 파일만 재업로드 → 4단계 반복              │
└──────────────────────────────────────────────────────────┘

대안: S3 이벤트 → Lambda → DB 자동 업데이트 (4단계 자동화)
```

---

## 4. Manifest Format

`manifest.csv` (CSV/JSON 둘 다 지원):

```csv
filename,sha256,size_bytes,grid_ix,grid_iy,stage_x_um,stage_y_um
tile_ix0_iy0.tif,a3f...,4194304,0,0,0.0,0.0
tile_ix0_iy1.tif,b7c...,4194308,0,1,0.0,150.0
tile_ix1_iy0.tif,c2e...,4194300,1,0,150.0,0.0
...
```

- `filename`, `sha256`, `size_bytes`: 필수
- `grid_ix`, `grid_iy`: 옵션 (없으면 mosaic은 sqrt(N) fallback)
- `stage_x_um`, `stage_y_um`: 옵션 (장비에서 추출 가능 시)

**Fallback**: manifest 없이 단순 업로드도 허용 (`POST /scans/{id}/upload-sessions/simple` 같은 별도 엔드포인트). 이 경우 grid/stage 는 NULL.

---

## 5. End-to-End Pipeline Flow

```
┌──────────────────────────────────────────────────────────┐
│ 0. Scan 생성: POST /scans (name, material)               │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 1. 업로드 (위 §3 워크플로우)                             │
│  upload_sessions + upload_items + images 채워짐          │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 2. 분석 시작                                             │
│  POST /analyses (scan_id, model_id, params)              │
│  생성: analyses 1행 (status=pending, steps_done={})      │
│       samples N행 (analysis × image)                     │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 3-6. Background → SAM → domain_stats → proximity         │
│  (v4 와 동일)                                            │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 7. 리뷰/라벨링 (사람, GUI)                               │
│  domain_labels 다중 라벨, flakes.label 큐레이션          │
└──────────────────────────────────────────────────────────┘
```

---

## 6. Tables at a Glance

| Table | Role | Filled by |
|---|---|---|
| `models` | LoRA 체크포인트 메타 | 사람 (모델 등록 시) |
| `scans` | 사용자 업로드 묶음 (= 실험 단위) | 사람 (스캔 생성 시) |
| `upload_sessions` | 업로드 배치 단위 + 진행률 | API (init 시) |
| `upload_items` | 파일 단위 업로드 상태/재시도 | API + S3 이벤트 |
| `images` | 성공한 이미지 + S3 위치 + 격자 좌표 | API (complete 시) |
| `analyses` | (scan, model, params) 분석 단위 | API + 워커 |
| `runs` | GPU/CPU 단계 실행 시도 (감사 로그) | 워커 |
| `samples` | analysis × image 컨테이너 | 워커 |
| `domains` | SAM 마스크 1개 = 1행, RLE 보존 | GPU 워커 |
| `flakes` | 닿는 도메인 그룹, 큐레이션 | CPU 워커 + 사람 |
| `domain_labels` | 사람이 단 다중 라벨 | 사람 (GUI) |

---

## 7. Key Design Decisions (v5)

```
✅ Upload tracking (옵션 B)
   • upload_sessions.status: active | completed | aborted
   • upload_items.status: pending | uploading | uploaded | failed
   • images 테이블에는 "성공한 이미지"만 들어감
     → 분석 쿼리는 'WHERE upload_status' 필터 불필요
   • 진행률: completed_files / total_files
   • Resume: pending+failed items 만 재업로드

✅ Tile position
   • images.grid_ix, grid_iy: mosaic 렌더링용 (SQL 인덱스로 빠름)
   • images.stage_x_um, stage_y_um: 실제 stage 좌표 (옵션, 향후 spatial)
   • Manifest 기반 ingest (옵션 b): 파일명 의존 제거

✅ scan : analysis = 1 : N
   • 일반: 1개 analysis 만 active
   • model_id 또는 params 다르면 새 analysis (LoRA 비교)
   • 중간 변경 (같은 model+params) → 기존 행 덮어쓰기

✅ analysis × image = sample (UNIQUE 제약)
✅ domain ↔ flake (N:1, singleton domain도 자기 flake 가짐)
✅ Storage 분리 (DB: 메타+RLE+라벨, S3: 원본+bg+parquet)
✅ Audit (runs 재시도 기록, analyses.steps_done 마지막 상태만)
✅ Labels (domain 다중 자유문자열, flake 단일 큐레이션, stats 자동예측 자리)
```

---

## 8. SQL DDL (v5-final draft)

```sql
-- 1) LoRA checkpoints
CREATE TABLE models (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    base_model TEXT NOT NULL,
    s3_uri TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) User upload batch (= 실험 단위)
CREATE TABLE scans (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    material TEXT,
    description TEXT,
    image_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT
);
CREATE INDEX ix_scans_material ON scans(material) WHERE material IS NOT NULL;

-- 3) Upload session (배치 단위, 진행률)
CREATE TABLE upload_sessions (
    id BIGSERIAL PRIMARY KEY,
    scan_id BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    total_files INT NOT NULL,
    completed_files INT NOT NULL DEFAULT 0,
    failed_files INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',     -- active|completed|aborted
    manifest_s3_uri TEXT,                      -- 원본 manifest 보관 위치
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT
);
CREATE INDEX ix_upload_sessions_scan ON upload_sessions(scan_id);

-- 4) Upload item (파일 단위 상태/재시도)
CREATE TABLE upload_items (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,
    sha256 CHAR(64) NOT NULL,
    filename TEXT NOT NULL,
    size_bytes BIGINT,
    status TEXT NOT NULL DEFAULT 'pending',    -- pending|uploading|uploaded|failed
    s3_uri TEXT,
    error TEXT,
    attempts INT NOT NULL DEFAULT 0,
    image_id BIGINT REFERENCES images(id),     -- 성공 후 연결
    -- manifest 에서 가져온 위치 정보
    grid_ix INT,
    grid_iy INT,
    stage_x_um REAL,
    stage_y_um REAL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE(session_id, sha256)
);
CREATE INDEX ix_upload_items_session ON upload_items(session_id);
CREATE INDEX ix_upload_items_status ON upload_items(session_id, status);

-- 5) Image registry (성공한 것만)
CREATE TABLE images (
    id BIGSERIAL PRIMARY KEY,
    scan_id BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    sha256 CHAR(64) NOT NULL,
    s3_uri TEXT NOT NULL,
    width INT NOT NULL,
    height INT NOT NULL,
    filename TEXT,
    grid_ix INT,                               -- 격자 컬럼 인덱스 (mosaic)
    grid_iy INT,                               -- 격자 행 인덱스
    stage_x_um REAL,                           -- 실제 stage 좌표 (μm)
    stage_y_um REAL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(scan_id, sha256)
);
CREATE INDEX ix_images_scan ON images(scan_id);
CREATE INDEX ix_images_grid ON images(scan_id, grid_ix, grid_iy)
    WHERE grid_ix IS NOT NULL AND grid_iy IS NOT NULL;

-- 6) Analysis
CREATE TABLE analyses (
    id BIGSERIAL PRIMARY KEY,
    scan_id BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    model_id BIGINT NOT NULL REFERENCES models(id),
    amg_params JSONB NOT NULL,
    bg_params  JSONB,
    proximity_params JSONB,
    steps_done JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    label TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_analyses_scan ON analyses(scan_id);

-- 7) Runs (단계별 시도 audit)
CREATE TABLE runs (
    id BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    instance_type TEXT,
    instance_id TEXT,
    spot BOOLEAN,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    metrics JSONB
);
CREATE INDEX ix_runs_analysis ON runs(analysis_id);

-- 8) Samples (analysis × image)
CREATE TABLE samples (
    id BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    image_id BIGINT NOT NULL REFERENCES images(id),
    bg_s3_uri TEXT,
    n_domains INT,
    n_flakes INT,
    UNIQUE(analysis_id, image_id)
);
CREATE INDEX ix_samples_analysis ON samples(analysis_id);

-- 9) Flakes (NOTE: created before domains because domains.flake_id FK references flakes)
CREATE TABLE flakes (
    id BIGSERIAL PRIMARY KEY,
    sample_id BIGINT NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    n_domains INT NOT NULL,
    bbox INT[] NOT NULL,
    area INT NOT NULL,
    segmentation_rle JSONB NOT NULL,
    label TEXT,
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    review_notes TEXT
);
CREATE INDEX ix_flakes_sample ON flakes(sample_id);

-- 10) Domains (SAM mask 1개 = 1행)
CREATE TABLE domains (
    id BIGSERIAL PRIMARY KEY,
    sample_id BIGINT NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    flake_id BIGINT REFERENCES flakes(id),
    bbox INT[] NOT NULL,
    area INT NOT NULL,
    segmentation_rle JSONB NOT NULL,
    sam_score REAL,
    stats JSONB
);
CREATE INDEX ix_domains_sample ON domains(sample_id);
CREATE INDEX ix_domains_flake ON domains(flake_id);

-- 11) Domain labels (다중 자유 문자열)
CREATE TABLE domain_labels (
    id BIGSERIAL PRIMARY KEY,
    domain_id BIGINT NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    labeled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    labeled_by TEXT,
    UNIQUE(domain_id, label)
);
CREATE INDEX ix_domain_labels_label ON domain_labels(label);
CREATE INDEX ix_domain_labels_domain ON domain_labels(domain_id);
```

---

## 9. Open Questions (확인 필요)

1. **`analyses.steps_done` 키 이름** — `bg / sam / domain_stats / proximity` 4개로 충분?
   stand-alone에는 thumbnails, selector, clustering, explorer도 있는데 GUI-only라 빼두는 방향이 자연스러움.

2. **`runs.step` 도 같은 키 사용** — `'background' / 'sam' / 'domain_stats' / 'proximity'` 4종 enum-like 문자열로 OK?

3. **`scans.created_by` / `domain_labels.labeled_by` / `upload_sessions.created_by`** — 인증 시스템 아직 없으면 nullable TEXT로 두고 나중에 user 테이블 추가 시 FK 전환할까요?

답변 받으면 alembic migration 작성 → `feat/react-fastapi-migration` 브랜치에 커밋.
