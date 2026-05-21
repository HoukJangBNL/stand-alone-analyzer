# Qpress SAM Pipeline — DB Schema v4

> **Status**: Draft (pending final 3 questions). Once confirmed, this becomes the source of truth for the initial alembic migration.
>
> **Stack**: PostgreSQL on RDS (`db.t4g.small`) + SQLAlchemy 2.x async + asyncpg + alembic.

---

## 1. Entity-Relationship Diagram

```
┌──────────────┐                    ┌──────────────┐
│   models     │                    │    scans     │
│──────────────│                    │──────────────│
│ id (PK)      │                    │ id (PK)      │
│ name UNIQUE  │                    │ name         │
│ base_model   │                    │ material     │
│ s3_uri       │                    │ description  │
│ description  │                    │ image_count  │
│ created_at   │                    │ created_at   │
└──────┬───────┘                    │ created_by   │
       │                            └──────┬───────┘
       │ N                                 │
       │                                   ├──────────────────┐
       │                                   │                  │
       │                                   ▼ N                ▼ N
       │                          ┌──────────────┐   ┌──────────────┐
       │                          │   images     │   │  analyses    │
       │                          │──────────────│   │──────────────│
       │                          │ id (PK)      │   │ id (PK)      │
       │                          │ scan_id (FK) │   │ scan_id (FK) │◄───┐
       │                          │ sha256       │   │ model_id (FK)│────┘
       └──────────────────────────┤ s3_uri       │   │ amg_params   │
                                  │ width        │   │ bg_params    │
                                  │ height       │   │ proximity_p. │
                                  │ filename     │   │ steps_done   │
                                  │ uploaded_at  │   │ status       │
                                  │              │   │ label        │
                                  │ UNIQUE(scan, │   │ notes        │
                                  │   sha256)    │   │ created_at   │
                                  └──────┬───────┘   │ updated_at   │
                                         │           └──────┬───────┘
                                         │                  │
                                         │ 1                │ 1
                                         │                  ├────────────┐
                                         │                  │            │
                                         │                  ▼ N          ▼ N
                                         │           ┌──────────┐  ┌──────────┐
                                         │           │  runs    │  │ samples  │
                                         │           │──────────│  │──────────│
                                         │           │ id (PK)  │  │ id (PK)  │
                                         │           │ analysis │  │ analysis │
                                         │           │ step     │  │ image_id ├────┐
                                         │           │ status   │  │ bg_s3_uri│    │
                                         │           │ instance │  │ n_domains│    │
                                         │           │ spot     │  │ n_flakes │    │
                                         │           │ started  │  │          │    │
                                         │           │ finished │  │ UNIQUE(  │    │
                                         │           │ error    │  │  anal,   │    │
                                         │           │ metrics  │  │  image)  │    │
                                         │           └──────────┘  └────┬─────┘    │
                                         │                              │          │
                                         └──────────────────────────────┼──────────┘
                                                                        │ 1
                                                                        ├──────────┐
                                                                        │          │
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
scans          1 ─────< N  images
scans          1 ─────< N  analyses        (보통 1, 모델/파라미터 다르면 여러개)
models         1 ─────< N  analyses
analyses       1 ─────< N  runs            (재시도/단계별)
analyses       1 ─────< N  samples         (이미지당 1개)
images         1 ─────< N  samples         (다른 analysis에서 재사용)
samples        1 ─────< N  domains
samples        1 ─────< N  flakes
flakes         1 ─────< N  domains         (한 flake = 여러 domain)
domains        1 ─────< N  domain_labels   (다중 라벨)
```

---

## 3. End-to-End Data Flow

```
┌──────────────────────────────────────────────────────────┐
│ 1. 업로드                                                │
│  user → POST /scans (name, material)                     │
│       → POST /scans/{id}/images (multipart, sha256)      │
│  생성: scans 1행, images N행                             │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 2. 분석 시작                                             │
│  user → POST /analyses (scan_id, model_id, params)       │
│  생성: analyses 1행 (status=pending, steps_done={})      │
│       samples N행 (analysis × image)                     │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 3. Background 단계 (CPU/Lambda)                          │
│  runs +1 (step='background')                             │
│  samples.bg_s3_uri 채움                                  │
│  analyses.steps_done.background = true                   │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 4. SAM 단계 (GPU spot, g6e.48xlarge)                     │
│  runs +1 (step='sam', instance_type, started_at...)      │
│  domains N행/sample (RLE, bbox, sam_score)               │
│  samples.n_domains 채움                                  │
│  analyses.steps_done.sam = true                          │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 5. Domain stats (CPU)                                    │
│  domains.stats JSONB 채움 (mean_intensity, contrast...)  │
│  analyses.steps_done.domain_stats = true                 │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 6. Proximity + Flake 형성 (CPU, parallel)                │
│  S3에 distances.parquet 저장 (DB 안 들어감)              │
│  flakes N행/sample 생성, domains.flake_id 업데이트       │
│  flakes.segmentation_rle = merged RLE                    │
│  samples.n_flakes 채움                                   │
│  analyses.steps_done.proximity = true                    │
└────────────────┬─────────────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 7. 리뷰/라벨링 (사람, GUI)                               │
│  domain_labels +N (다중 라벨, 자유 문자열)               │
│  flakes.label / flakes.reviewed 업데이트                 │
└──────────────────────────────────────────────────────────┘
```

---

## 4. Tables at a Glance

| Table | Role | Filled by |
|---|---|---|
| `models` | LoRA 체크포인트 메타 | 사람 (모델 등록 시) |
| `scans` | 사용자 업로드 묶음 (= 실험 단위) | 사람 (업로드 시) |
| `images` | 원본 이미지 + S3 위치 | API (업로드 시) |
| `analyses` | (scan, model, params) 분석 단위, 진행상태 | API + 워커 |
| `runs` | GPU/CPU 단계 실행 시도 (감사 로그) | 워커 |
| `samples` | analysis × image 컨테이너, per-image 결과 | 워커 (분석 시작 시) |
| `domains` | SAM 마스크 1개 = 1행, RLE 보존 | GPU 워커 |
| `flakes` | 닿는 도메인 그룹 (legacy "island"), 큐레이션 | CPU 워커 + 사람 |
| `domain_labels` | 사람이 단 다중 라벨 | 사람 (GUI) |

---

## 5. Key Design Decisions

```
✅ scan : analysis = 1 : N
   • 일반: 1개 analysis 만 active
   • model_id 또는 params 다르면 새 analysis (LoRA 비교)
   • 중간 변경 (같은 model+params 내) → 기존 행 덮어쓰기,
     steps_done 뒷단 invalidate

✅ analysis × image = sample (UNIQUE 제약)
   • 같은 image가 다른 analysis에서 재처리 가능
   • bg_s3_uri 등 per-image 결과는 sample에 귀속

✅ domain ↔ flake
   • domain.flake_id 로 N:1 (flake가 도메인을 집합)
   • singleton domain도 자기만의 flake 하나 가짐
     (stand-alone 규칙)

✅ Storage 분리
   • DB: 메타데이터, RLE (segmentation), 라벨, status
   • S3: 원본 이미지, bg-corrected,
     distances.parquet, flake_assignments.parquet

✅ Audit
   • runs 테이블이 GPU/CPU 단계별 시도를 모두 기록
     (재시도 포함)
   • analyses.steps_done JSONB 는 "현재 어디까지 됐나" 만 담음
     (히스토리 X)

✅ Labels
   • domain_labels: 다중 라벨, 자유 문자열
     (frontend autocomplete 으로 표기 통일 유도)
   • flakes.label: 단일 string, 큐레이션용
   • domains.stats: 모델 자동 예측 (예: layer_pred) 자리,
     사람 라벨과 분리
```

---

## 6. SQL DDL (v4-final draft)

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

-- 2) User upload batch
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

-- 3) Image registry
CREATE TABLE images (
    id BIGSERIAL PRIMARY KEY,
    scan_id BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    sha256 CHAR(64) NOT NULL,
    s3_uri TEXT NOT NULL,
    width INT NOT NULL,
    height INT NOT NULL,
    filename TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(scan_id, sha256)
);
CREATE INDEX ix_images_scan ON images(scan_id);

-- 4) Analysis
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

-- 5) Runs
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

-- 6) Samples
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

-- 7) Flakes (NOTE: created before domains because domains.flake_id FK references flakes)
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

-- 8) Domains
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

-- 9) Domain labels (multi-label, free-form)
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

## 7. Open Questions (확인 필요)

1. **`analyses.steps_done` 키 이름** — `bg / sam / domain_stats / proximity` 4개로 충분?
   stand-alone에는 thumbnails, selector, clustering, explorer도 있는데 GUI-only라 빼두는 방향이 자연스러움.

2. **`runs.step` 도 같은 키 사용** — `'background' / 'sam' / 'domain_stats' / 'proximity'` 4종 enum-like 문자열로 OK?

3. **`scans.created_by` / `domain_labels.labeled_by`** — 인증 시스템 아직 없으면 nullable TEXT로 두고 나중에 user 테이블 추가 시 FK 전환할까요?

답변 받으면 alembic migration 작성 → `feat/react-fastapi-migration` 브랜치에 커밋.
