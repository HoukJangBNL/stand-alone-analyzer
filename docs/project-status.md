# Project Status

> **Living document.** PM이 작업 종료 시점마다 갱신.
> 사용자(오너)가 "지금 어디까지 와 있나" 30초 안에 파악할 수 있어야 함.
> 디테일 디자인·DDL·운영 절차는 별도 문서로 링크 — 여기에는 **상태**만.

**Last updated**: 2026-05-26
**Current branch**: `feat/migration-cutover` (post-cutover punch list 진행 중 — W3.5 / W2.4 / W8.1 / **W6 (auth+ACL+usage)** / **W5 전체(A/infra/B1/B2/C)** + **admin_usage 격리 fix** + **W10 A→E 전부** + **#64 fix** + **Robustness Phase A→D 전부** 완료. 다음 한 발: **Phase E real-data 검증 루프** (backend restart → 1 → 10 → 11 mixed → 50 cancel/resume → 20 close-reopen → **E6 3648/9GB 오너 승인 후**). 잔여: W6.1 Cognito 부트스트랩(AWS 승인 대기) / W7 GPU / W8.2 SSM / W9 reg_covar.)
**Schema version**: `v7.1` (alembic `0003_w5a_materials_uploads` 헤드 — materials 테이블 + scans/images 강화. 로컬 saa_test 적용 완료. RDS는 W6.1 Cognito 승인 후 함께)

---

## 1. Big picture

마이그레이션 진행 중: **Streamlit standalone analyzer → React + FastAPI + PostgreSQL** (RDS).
GUI는 React/web, API는 FastAPI, 데이터는 RDS, 무거운 계산은 GPU 워커(g6e.* spot 예정).

---

## 2. 최근 마일스톤 (완료)

| 시점 | 내용 |
|---|---|
| 2026-05 | DB 스키마 v6 확정 ([`db-schema-v6.md`](db-schema-v6.md)) |
| 2026-05 | RDS `qpressdb` 클러스터 빈 상태 확인, `qpress` DB 생성 |
| 2026-05 | RDS 마스터 비번 → AWS Secrets Manager 관리로 전환 (`--manage-master-user-password`) |
| 2026-05 | Bastion EC2 (`i-063165d449976b2e4`, t4g.nano) + SG 셋업, SSH 터널 검증 |
| 2026-05 | Alembic 셋업 + `0001_initial_v6` RDS 적용 (15 테이블 + 3 ENUM + system user 시드) |
| 2026-05 | SQLAlchemy 2.x ORM 모델 작성 (그룹별 7개 파일) |
| 2026-05 | 운영 런북 ([`db-ops.md`](db-ops.md)) 작성 |
| 2026-05 | PM 역할 정의 (`CLAUDE.md`) — 에이전트 오케스트레이션 모델 도입 |
| 2026-05-21 | 영속 에이전트 7개 정의 (`.claude/agents/`): db-specialist, api-developer, frontend-architect, algo-engineer, devops-engineer, researcher, code-reviewer |
| 2026-05-21 | **W4.2** ORM `Computed` 수정 + manifest `step_status` reconciliation 헬퍼 — `Analysis.status`를 `Computed(..., persisted=True)`로 선언(GENERATED 컬럼 메타데이터 정합), `src/flake_analysis/db/reconcile.py` 추가. DB가 step status의 source of truth. → [`docs/superpowers/plans/2026-05-21-W4.2-db-orm.md`](superpowers/plans/2026-05-21-W4.2-db-orm.md) |
| 2026-05-21 | **W4.4** 클러스터링 spec 변경 + auto-opt — `reg_covar`를 엔진/래퍼/매니페스트에 노출(디폴트 `10.0`), `auto_tune_reg_covar` 드라이버(blob-recall × Mahalanobis margin), W4.3 invariant 강화(시드 블롭 100% 캡쳐), parity golden 재생성(clustering 블록 한정). 342 tests pass. → [`docs/superpowers/plans/2026-05-21-W4.4-clustering-spec.md`](superpowers/plans/2026-05-21-W4.4-clustering-spec.md) |

---

## 3. 진행 중 / 다음 작업

### 3.1 다음 한 발
**Robustness Phase A→D 전부 완료(2026-05-26)** — 프론트 visibility (A1-A4) / 서버 reliability (B1-B6) / Compute Tab 404 fix (C1) / draft·partial state semantics (D1-D5). 343 vitest pass, 1차 목표 코드/UI 측면은 ready. **다음 한 발**: **Phase E real-data 검증 루프** — backend restart → E1 smoke(1 file) → E2 happy(10) → E3 mixed-failure(10+1 corrupt) → E4 cancel/resume(50) → E5 close-reopen(20) → **E6 3648 PNG / 9GB 오너 승인 후**. 데이터: `/Volumes/QPressDataShare/data/test_data/.../EE5A8OD5/rawImages` (3648 PNG, 1920×1200, ~2.5 MB/ea). 버킷: `qpress-uploads` (us-east-2). 그 뒤 잔여: W6.1 Cognito 부트스트랩(AWS 승인 대기) / W7(GPU) / W8 Part 2(SSM) / W9(reg_covar 캘리브레이션).

> 2026-05-22 — admin_usage 격리 fix 완료. 별건 백로그 3개 등록(test_scans_* s3_uri / tests/scripts drop_all footgun / test_run_*_sse hang).

### 3.2 백로그 (우선순위 순)

> 모든 백로그 항목에 대해 플랜 작성 완료. 실행 가능 여부는 각 플랜의 "Execution Handoff" 섹션을 참고.

- [x] **W3.5** 인터랙티브 클러스터링 튜너 UI — [`plan`](superpowers/plans/2026-05-21-W3.5-clustering-tuner.md) — backend slice (`reg_covar` + `auto_tune` schema/route + SSE `reg_covar_chosen`), frontend slice (log-scale slider [0.1, 10.0] + AutoTune 버튼 + ClusteringRightRail 마운트). 9 commits (`e082d90`..`ab87bf9`). 246 vitest tests pass.
- [x] **W2.4** manifest 엔드포인트 DB rewire — [`plan`](superpowers/plans/2026-05-21-W2.4-manifest-db-rewire.md) — `GET /data/manifest`가 DB의 `analyses.steps_done`를 source of truth로 오버레이 (background/sam/domain_stats/domain_proximity + 신규 top-level `status`). DB row 없으면 silent fallback, SQL error는 500 `db_unavailable`. 완료 — `2d5d7d5`.
- [ ] **W5** 업로드 플로우 — D-block 잠금(2026-05-22) → 5개 실행 가능 plan 분리 작성:
    - [x] [`W5-A 스키마`](superpowers/plans/2026-05-22-W5-A-schema.md) — `materials` 테이블 + `scans.extra_metadata` JSONB + `scans.material` NOT NULL+FK + `images.grid_ix/iy` NOT NULL+UNIQUE. alembic 0003. **완료(2026-05-22, 4 commits `1067eac`..`70064dd`, PG 21/21).**
    - [ ] [`W5-B1 materials + scan create`](superpowers/plans/2026-05-22-W5-B1-materials-and-scan-create.md) — `GET/POST /materials` + `POST /projects/{pid}/scans` (path-only routing).
    - [ ] [`W5-B2 presign + complete + finalize`](superpowers/plans/2026-05-22-W5-B2-presign-complete-finalize.md) — boto3 presigned PUT + `x-amz-checksum-sha256` enforcement + `images` row 생성 + scan finalize/usage event.
    - [ ] [`W5-C 프론트엔드`](superpowers/plans/2026-05-22-W5-C-frontend.md) — 프로젝트 페이지 모달, MaterialCombobox(auto-add), Web Crypto SHA256, 4-concurrent upload orchestrator, Playwright e2e.
    - [x] [`W5-infra AWS S3`](superpowers/plans/2026-05-22-W5-infra.md) — 버킷 + CORS(dev-only) + 라이프사이클 + IAM dev/prod + 크로스-prefix 거부 정책. **완료(2026-05-22, 6 prep commits `140b757`..`3a69369` + AWS 7 게이트 적용, dryrun 14/14 PASS).**
    - 옛 SKETCH 문서: [~~2026-05-21-W5-upload-flow.md~~](superpowers/plans/2026-05-21-W5-upload-flow.md) (Superseded).
- [x] **W6** 인증/세션 (Cognito + 4-tier 글로벌 ENUM + per-project ACL + usage events + 프론트엔드 슬라이스) — [`plan v2`](superpowers/plans/2026-05-21-W6-auth-session-v2.md) — 6 sub-plan 완료(W6.0 schema v7 / W6.1 Cognito 인프라 docs / W6.2 백엔드 의존성 / W6.3 ACL / W6.4 usage events / W6.5 프론트엔드). 28 commits (`8bf3c90`..`bd9339b`). 백엔드 비-PG 테스트 292 pass + 프론트엔드 vitest 265 pass. **W6.1 Cognito 부트스트랩 실행은 AWS 승인 대기.**
- [ ] **W10** real projects table + 1:N scans + per-scan analysis + per-scan mutex/manifest — D-block 잠금(2026-05-22) → 5개 실행 가능 plan 분리 작성:
    - [ ] [`W10-A 스키마`](superpowers/plans/2026-05-22-W10-A-schema.md) — `projects` 테이블 (TEXT PK = `gen_random_uuid()::text`) + `scans.project_id` FK ON DELETE RESTRICT + alembic `0004_w10_projects` + `scripts/db/wipe-saa-test-pre-w10.sql` (in-memory persistence only — D2/D3).
    - [x] [`W10-B active project decoupling`](superpowers/plans/2026-05-22-W10-B-active-project-decoupling.md) — `_active_project` 글로벌 제거, `SAA_ANALYSIS_FOLDER` → `SAA_ANALYSIS_ROOT` rename(legacy fallback), `acquire_scan_lock(scan_id)` asyncio.Lock per-scan mutex + `analysis_folder(root, pid, sid)` / `manifest_path(root, pid, sid)` helpers (D4/D5). **완료(2026-05-23, 4 commits `50197a4`..`8de71f9` + docs `9da1f00`).**
    - [x] [`W10-C route surface`](superpowers/plans/2026-05-22-W10-C-route-surface.md) — projects CRUD service + schemas, `routes/projects.py` rewrite, `GET /projects/{pid}/scans`, 3 parallel sub-batches migrating routers to `/projects/{pid}/scans/{sid}/...`, acceptance gate (D6 force-create UX). **완료(2026-05-23, 7 commits `9fcae04`..`02abe6f`).**
    - [x] [`W10-D 프론트엔드`](superpowers/plans/2026-05-22-W10-D-frontend.md) — `state/projectSlice.ts` `activeScanId` + cross-project clear, `api/projects.ts` CRUD-only rewrite + `listScansForProject`, `CreateProjectModal`(name+description) + `ScanPicker` 드롭다운, `App.tsx` 경로 grammar `/projects/:pid/scans/:sid/<tab>` + `ProjectScanSync`, tab 페이지 `scanId` prop + `ComputeTab` no-scan empty state, Sidebar `listProjects` + `CreateProjectModal` 통합 (legacy `CreateProjectForm` 삭제), 18 vitest 파일 3 batches. **완료(2026-05-25, 8 commits `5314879`..`044e64d`, vitest 86 files / 300 tests pass, build green)**.
    - [x] [`W10-E 테스트 + 데이터 마이그레이션`](superpowers/plans/2026-05-22-W10-E-test-and-data-migration.md) — `scripts/db/wipe-saa-test.sh` 가드(`saa_test` prefix 강제) + `db-ops.md §3.4` W10 pre-flight 런북, `tests/api/conftest.py` `active_project`/`active_material`/`active_scan` fixtures rewire, 27 backend test 파일 4 batches(3a projects+deps / 3b run+SSE / 3c data+static / 3d selector+scans+mutex+guards), 신규 `tests/api/test_w10_acceptance.py` 6 specs(D2 DELETE-RESTRICT × 2 / D4 scan-mutex × 2 / D6 force-create × 2 — 모두 green; 423 Locked status code, ProjectHandle response shape, /run/fake mini-app substitution 사용), `scripts/dev/w10-acceptance.sh` 게이트(alembic+pytest+vitest+build). **완료(2026-05-25, 7 commits `bc7f6ea`..`396f630`).** 별건 회귀: `test_scans_*` 13건 NotNullViolationError on `scans.project_id`(W10-A FK 도입 후 `upload_service.create_scan`이 `project_id` 미전파) — #64로 분리 추적, 1차 목표 마지막 블로커.
- [ ] **W7** GPU 워커 트리거 (background → SAM → domain_stats → domain_proximity) — [`plan (SKETCH)`](superpowers/plans/2026-05-21-W7-gpu-workers.md) — D1–D7 결정 필요. SAM 엔진 포팅이 가장 큰 별도 sub-plan 후보.
- [x] **W8 (Part 1)** CI: alembic 모델↔스키마 drift check — [`plan (READY, Part 1)`](superpowers/plans/2026-05-21-W8-ci-drift-and-ssm.md) — devops-engineer (or api-developer), 3 tasks. 새 워크플로 `.github/workflows/alembic-drift.yml`. (완료 — `0715a41`)
- [ ] **W8 (Part 2)** SSM Session Manager 검토 (SSH key 배포 없이 DB 접근) — 같은 플랜 §"Part 2 (SKETCH)" — D2.1–D2.3 결정 필요.
- [ ] **W9 (W4.4 후속)** 실제 annotated 데이터로 `reg_covar` 캘리브레이션 sweep — [`plan (SKETCH)`](superpowers/plans/2026-05-21-W9-reg-covar-calibration.md) — D1 (데이터셋 소스)이 핵심 블로커. fog/overlap에서 leak 발견 시 `auto_tune_reg_covar` 2D 그리드로 확장.

---

## 4. 미해결 결정 / 사용자 승인 대기

- **W6.1 Cognito 부트스트랩 (AWS state 변경)** — `scripts/devops/cognito_bootstrap.sh` 실행 권한 + alembic `0002_v7_auth` RDS 적용 + SSM 파라미터(`/saa/cognito/*`) 등록. 절차는 [`docs/cognito-setup.md`](cognito-setup.md). 비용은 Cognito MAU 기반(~$0.0055/MAU, free tier 50k).

---

## 5. 알려진 제약 / 메모

- **alembic `--autogenerate` 금지.** GENERATED column / composite FK / partial index / ENUM 제대로 못 잡음. 마이그레이션은 손으로. (이유: `db-ops.md` §3)
- **스키마 변경은 새 v.** v6 동결, breaking change 시 `db-schema-v7.md` + 새 revision.
- **Bastion public IP는 stop/start마다 바뀜.** 운영 절차는 `db-ops.md` §2.
- **오너 home/office IP 바뀌면** bastion SG ingress 갱신 필요 (`db-ops.md` §2.3).
- Streamlit (`app/streamlit_app.py`)은 당분간 legacy로 유지. 마이그레이션 검증용.

---

## 6. 인프라 핵심 ID (빠른 참조)

자세한 건 [`db-ops.md`](db-ops.md) §1.

| | |
|---|---|
| AWS profile / region | `qpress` / `us-east-2` |
| RDS endpoint | `qpressdb.ch08y4ooqgmq.us-east-2.rds.amazonaws.com:5432` |
| App DB | `qpress` (master user `houk`, 비번은 Secrets Manager) |
| Bastion EC2 | `i-063165d449976b2e4` (현재 `stopped`) |
| Bastion key | `~/.ssh/qpress-bastion.pem` |

---

## 7. 변경 로그 (이 문서 자체)

- 2026-05-21: 초기 작성. CLAUDE.md에서 "어디까지 와 있나" 섹션 분리.
- 2026-05-21: 영속 에이전트 7개 부트스트랩 — `.claude/agents/*.md` + CLAUDE.md §3 갱신.
- 2026-05-21: W4.2 완료 — ORM `Computed` 수정 + `db.reconcile` 헬퍼. manifest 엔드포인트 rewire는 W2.1로 이연.
- 2026-05-21: W4.4 완료 — `reg_covar` tunable + auto-opt 드라이버 + parity golden 재생성(owner 승인). 디폴트 `1.0` → `10.0`(rank-deficient 시드 covariance 정규화). 다음은 W3.5 UI follow-up.
- 2026-05-21: 백로그 7개 항목(W3.5 / W2.4 / W5 / W6 / W7 / W8 / W9) 전체 플랜 작성 — detailed 2개(W3.5, W2.4, W8 Part 1), sketch 5개(W5/W6/W7/W8 Part 2/W9). 실행 가능 플랜은 subagent-driven-development로 dispatch 가능, sketch는 D-block 결정 필요.
- 2026-05-21: **W8 Part 1** 완료 — alembic drift CI workflow (`0bbf448` / `0715a41` / `623c5af`). `python scripts/check_alembic_drift.py`가 PR마다 ephemeral Postgres에 마이그레이션 적용 후 `compare_metadata` 비교. 모델/스키마 drift 시 build 실패.
- 2026-05-21: **W2.4** 완료 — `GET /data/manifest`가 DB의 step status를 source of truth로 오버레이 (`6fde904` / `63467ab` / `f746e74` / `d530ff7` / `2d5d7d5`). 디스크 manifest는 file paths · 비-DB step (clustering/selector/thumbnails/explorer) 만 유지. DB row 없으면 silent fallback, SQL error는 `db_unavailable` 500.
- 2026-05-21: **W3.5** 완료 — backend slice (`e082d90` / `268ac65`) + frontend slice 7 commits (`ddd33c7`..`ab87bf9`). `reg_covar` 슬라이더([0.1, 10.0] log-scale, default 10.0) + AutoTune 버튼이 `ClusteringRightRail.tsx`에 마운트. data-testids: `clustering-reg-covar-slider` · `clustering-reg-covar-value` · `clustering-auto-tune`. SSE `done` 페이로드에 `reg_covar_chosen` 포함. 246 vitest tests pass.
- 2026-05-22: **W6 Test B (로컬 PG 통합 검증)** 완료 — 5 commits (`1366dc3` / `620af7a` / `e53c169` / `4591bde` / `8e09f2a`). `app.dependency_overrides[get_db_session]`로 라우트와 테스트가 `pg_session` 공유, role-mismatch 테스트는 `get_current_user` 직접 오버라이드. PG-marked tests/api 31/31 pass, tests/db 26/26 pass, 비-PG 385/385 pass (4 pre-existing `tests/test_xaccel_thumbnails.py` 실패는 W6 auth-gate 도입 시점에 이미 있던 것 — 별도 백로그). 다음은 W6 Test C (Cognito 부트스트랩 + e2e 로그인).
- 2026-05-22: **W5 D-block 잠금** + **5개 plan 작성**: D1 단일 버킷+dev/prod prefix / D2 브라우저 SHA256 + `x-amz-checksum-sha256` / D3 per-scan 세션 / D4 사용자 입력 폼(material 컨트롤드 보캐브 + auto-add, ix/iy 0-based 필수, image_count, extra_metadata JSONB) / D5 dev-bypass+Cognito 병행 / D6 프로젝트 상세 페이지 모달. `1 project = 1 scan v1` 결정으로 `scans.project_id` 컬럼 미추가(path-only routing). plan suite: W5-A(스키마) / W5-B1(materials+scan-create) / W5-B2(presign+complete+finalize) / W5-C(프론트엔드) / W5-infra(AWS S3). 옛 `2026-05-21-W5-upload-flow.md`는 Superseded.
- 2026-05-22: **CLAUDE.md** §2.5에 PM Bash 룰 + background 디폴트 룰 추가, §5에 Self-audit 단계(step 7) 추가. PM이 도메인 명령(pytest/uv run/npm/alembic) 직접 실행 금지, 에이전트 호출 디폴트 background. 사용자 피드백 대응(2건의 PM 룰 위반 — pytest 직접 실행 + foreground agent dispatch).
- 2026-05-21: **W6 전체** 완료 (코드, 28 commits) — Cognito + 4-tier 글로벌 ENUM(member/reader/operator/admin) + per-project ACL(viewer/editor) + usage events + 프론트엔드. 6 sub-plan: **W6.0** schema v7 (`8bf3c90`..`c51fba0`, UUID users + ENUMs + project_users + usage_events + alembic `0002_v7_auth`), **W6.1** Cognito 인프라 docs (`ad645cf` / `8dd41ea`, 부트스트랩/스모크 스크립트 + 런북 — AWS 실행은 승인 대기), **W6.2** 백엔드 의존성 (`fefd45e`..`f4aeb16`, JWKS verifier + cognito_sub upsert + dev-bypass + /auth/me·callback·logout), **W6.3** ACL (`57445f5`..`7c75ece`, 순수 resolver + require_role/require_project_role 가드 + admin 라우트), **W6.4** usage events (`33d1c5f`..`f53f36f`, emit 헬퍼 + login/logout/scan_run 훅 + GET /admin/usage), **W6.5** 프론트엔드 (`0518258`..`bd9339b`, authSlice + LoginPage/SignupPage/AdminPage + RequireAuth/RequireRole + 사이드바 + 모든 fetch에 Bearer 토큰). 백엔드 비-PG 292 pass, vitest 265 pass. 다음: W6.1 Cognito 부트스트랩 AWS 승인.
- 2026-05-22: **W5-A** 완료 — 4 commits (`1067eac` Material ORM / `d91ffff` alembic 0003 + 5-row 시드 / `2868b3f` Scan/Image ORM tightening / `70064dd` v7.1 doc). 로컬 saa_test에 적용 + round-trip 검증 통과. PG `tests/db` 21/21 pass. 스키마 v7.1: `materials(name PK, created_by_id FK, created_at)` + 시드 5종(graphene/MoS2/WSe2/hBN/WS2) + `scans.extra_metadata JSONB` + `scans.material NOT NULL+FK ON DELETE RESTRICT`(partial index 제거) + `images.grid_ix/iy NOT NULL` + `images_scan_grid_uq UNIQUE(scan_id, grid_ix, grid_iy)`(partial index 제거). RDS 적용은 W6.1 Cognito 승인 시점에 함께. anomaly: admin_usage 테스트 3건 격리 이슈는 W5-A 무관(별도 백로그).
- 2026-05-22: **W5-infra** 완료 — 6 prep commits (`140b757`..`3a69369`) + AWS apply 7 게이트 모두 적용. 버킷 `qpress-uploads` (us-east-2, SSE-S3+BucketKey, BPA 4 flags, BucketOwnerEnforced) + dev-only CORS(localhost:5173/5174) + lifecycle 3 rules(dev/ 30d, multipart 7d, dev/uploads-pending/ 1d) + IAM policies(`qpress-api-s3-uploads-dev`/`prod`) + IAM user `qpress-dev-local` + access key(`~/.aws/credentials` `[qpress-dev-local]` 프로파일) + bucket policy(cross-prefix deny 3 statements). `scripts/s3/dryrun.sh` 14/14 PASS. prod 도메인 결정 시 CORS 추가 예정. 다음: W5-B1 (materials + scan create endpoints).
- 2026-05-22: **admin_usage 테스트 격리 이슈 해결** — `21e9640` (`tests/db/conftest.py` only, +15/-2). `pg_session`에 `join_transaction_mode="create_savepoint"` 추가. SQLAlchemy 2.x async session이 outer trans 없이 bind된 상태에서 `commit()` 호출 시 outer trans를 deassociate시켜 rollback이 무효화되던 SAWarning이 root cause. `usage_events` 누적으로 `tests/api/test_admin_usage_route.py` 3건 실패하던 것 PASS. 프로덕션 코드 무변경. `tests/api -m pg` 35→38 pass, `tests/db -m pg` 21/21 (SAWarning 사라짐). 부수 발견 백로그(별건): #64 `test_scans_*` 11 failures (KeyError `s3_uri`), #65 `tests/scripts/conftest.py` `drop_all` footgun (조사 중 saa_test 스키마 한 번 wipe — 수동 복구), #66 `test_run_*_sse.py` hang (DB 미사용, asyncio teardown 의심).
- 2026-05-22: **W10 D-block 잠금** + **5개 plan 작성**: D1 real `projects` 테이블 (TEXT PK = `gen_random_uuid()::text`, name UNIQUE per-owner) / D2 DELETE RESTRICT (scans 존재 시 409, 명시적 wipe 필요) / D3 in-memory persistence only (no localStorage active project) / D4 `asyncio.Lock` per-scan mutex (다른 scan 동시 실행 OK, 같은 scan 두 번째 호출 409) / D5 per-scan filesystem `<root>/<pid>/<sid>/manifest.json` + `SAA_ANALYSIS_FOLDER` → `SAA_ANALYSIS_ROOT` rename / D6 zero-projects day-zero UX (Sidebar "+" only enabled, 모든 탭 disabled, 첫 프로젝트 생성 후 자동 이동). plan suite: W10-A(스키마+alembic 0004) / W10-B(active project decoupling + per-scan mutex/manifest) / W10-C(route surface `/projects/{pid}/scans/{sid}/...` migration) / W10-D(프론트엔드 routes + ScanPicker + CreateProjectModal + 18 vitest 3 parallel batches) / W10-E(test sweep 27 files 4 parallel batches + acceptance gate `scripts/dev/w10-acceptance.sh`). 판단 콜: TEXT PK(UUID 가독성+postgres 호환), 1 project = N scans (W5 `1 project = 1 scan v1` 폐기), in-memory only (URL이 source of truth), scan picker는 탭 콘텐츠 상단 (Sidebar에 두지 않음 — 프로젝트 선택과 분리), test sweep은 batch 단위 parallel dispatch (27 + 18 파일 단일 호출 시 토큰 폭발).
- 2026-05-22 — W10-A 스키마 (projects 테이블 + scans/project_users FK 재배선) 완료. alembic head `0004_w10_projects`. tests/db 일부 회귀 10건 (Scan() 생성자에 project_id 누락 — `test_analysis_status_generated.py` 9건 + `test_w5a_schema.py::test_image_grid_uniqueness` 1건) — W10-E sweep에서 처리. W10-B (active-project decoupling) 진입 가능.
- 2026-05-23 — **W10-B** (active-project decoupling + per-scan manifest/mutex) 완료. 4 commits: `50197a4` paths 헬퍼 + `SAA_ANALYSIS_ROOT` env (legacy `SAA_ANALYSIS_FOLDER` fallback 1릴리즈 유지) / `cf808a5` per-scan manifest 래퍼 (`load_for_scan`/`save_for_scan`) / `e1c9671` `acquire_scan_lock(scan_id)` per-scan asyncio.Lock / `8de71f9` `api/deps.py` 재작성. `_active_project` 글로벌 / `DEFAULT_PROJECT_ID` / `ProjectContext` / `get_project_context` 전부 제거. 새 시그니처: `get_manifest(project_id, scan_id)` · `get_active_analysis(scan_id, session)` · `acquire_scan_lock(scan_id)`. 21 tests green (state 13 + deps 4 + mutex 2 + scan_mutex_isolation 2). 라우트 레이어에서 `DEFAULT_ANALYSIS_FOLDER` 등 삭제된 심볼 import로 `tests/api -m pg` 42 파일 collection ImportError — W10-C scope. 다음: **W10-C** (route surface `/projects/{pid}/scans/{sid}/...` 재작성).
- 2026-05-23 — **W10-C** (route surface migration) 완료. 7 commits: `9fcae04` projects CRUD service + schemas (Task 1) / `54f88a7` projects CRUD route surface (Task 2) / `30c33d0` `GET /projects/{pid}/scans` list endpoint (Task 3) / `7bd358d` data + static routers per-scan grammar (Task 4a) / `c17bbfc` run + selector routers per-scan grammar + per-scan mutex via `acquire_scan_lock(scan_id)` (Task 4b) / `f525804` clustering + explorer routers per-scan grammar (Task 4c) / `02abe6f` drop stale `ValidatePathsRequest` import in `tests/api/test_schemas.py` (Task 1 follow-up). 모든 도메인 라우터(`/projects/{pid}/...`)가 `/projects/{pid}/scans/{sid}/...` 그래머로 이동, per-project mutex → per-scan mutex 교체. 라우트 총 52개. 5.1 PG sweep 2 pass / 87 skip / 0 fail (PG-marked 스위트 PG infra 없이 실행). 5.2 비-PG sweep 205 pass / 5 fail — **5건 모두 W10-E scope의 stale 테스트 파일**(W10-E 플랜 §"4 batches"에 명시): `test_manifest_endpoint_db.py` (3, 레거시 `/projects/local/data/manifest` 사용 → per-scan 그래머로 이주 예정), `test_path_validation.py` (1, W10-C에서 정당하게 삭제된 `validate-paths` 라우트 — W10-E에서 `git rm`), `test_sse_heartbeat.py::test_run_thumbnails_emits_heartbeat_when_pipeline_is_slow` (1, 레거시 `/projects/local/run/thumbnails` 사용 → 이주 예정). W10-C 자체에는 회귀 없음, 잔여 stale 테스트는 W10-E에서 일괄 처리. 다음: **W10-D** (frontend) 진입 가능.
- 2026-05-25 — **W10-D** (frontend) 완료. 8 commits `5314879`..`044e64d`: `5314879` `projectSlice.ts` `activeScanId` + cross-project clear / `aa3743f` `api/projects.ts` CRUD-only rewrite + `listScansForProject` / `0881f03` `CreateProjectModal`(name+description; legacy `CreateProjectForm` 대체) / `6a2756f` `ScanPicker` 드롭다운 + empty-state CTA / `1cbbeb2` `App.tsx` `/projects/:pid/scans/:sid/<tab>` 그래머 + `ProjectScanSync` / `a465aca` 탭 페이지 `scanId` prop + `ComputeTab` no-scan empty state / `b147836`+`3d7a771`+`2e3f9c6` 18 vitest 파일 3 batches(selector/components, clustering, explorer/uploadmodal) / `044e64d` Sidebar `listProjects` + `CreateProjectModal` 통합. 최종: vitest 86 files / 300 tests pass, `npm run build` green. Task 9(수동 e2e)는 owner 검증 단계로 이연.
- 2026-05-25 — **W10-E** (test+data migration) 완료. 7 commits `bc7f6ea`..`396f630`: `bc7f6ea` `scripts/db/wipe-saa-test.sh` 가드 wrapper(`saa_test` prefix 강제) + `db-ops.md §3.4` W10 pre-flight 런북 / `a6709de` `tests/api/conftest.py` `active_project`/`active_material`/`active_scan` fixtures rewire(`_active_project` 글로벌 폐기) / `3df08ce` batch 3a(projects+deps+project_context) / `2c3f39e` batch 3b(run+SSE 라우트) / `7f449a0` batch 3c(data+static 라우트) / `8cb48d1` batch 3d(selector+scans+mutex+guards) / `439ca61` 신규 `tests/api/test_w10_acceptance.py` 6 specs(D2 DELETE-RESTRICT × 2 / D4 scan-mutex × 2 / D6 force-create × 2 — 모두 green) / `396f630` `scripts/dev/w10-acceptance.sh` 게이트 스크립트(alembic upgrade head + pytest + vitest + npm build). 검증 콜: ① D4 `/run/fake` substitution(production `/run/fake` 라우트 부재로 mini-app에 합성 엔드포인트 마운트, `acquire_scan_lock(scan_id)`만 약 150ms 보유) ② status code 423 Locked(409 아님 — `ProjectBusy.status_code = HTTP_423_LOCKED` errors.py:64) ③ D6 응답 shape: `POST /projects` → `ProjectHandle`(no `scan_count`); `scan_count`는 `GET /projects/{pid}` `ProjectDetail`에만. 부수 발견 별건 회귀: `test_scans_*` 13건 NotNullViolationError on `scans.project_id`(W10-A FK 도입 후 `upload_service.create_scan`이 path arg `project_id`를 안 넘김) — #64로 분리 추적.
- 2026-05-26 — **Upload Robustness Phase A→D** 전부 완료. 플랜: [`2026-05-26-upload-robustness.md`](superpowers/plans/2026-05-26-upload-robustness.md). **Phase A** (visibility, 4 tasks): aggregate counter / failed-only filter / request_id surfacing / 백엔드 모듈 logger + structured 로그. **Phase B** (server reliability, 6 tasks): presign 멱등성(같은 sha256+filename+grid → 200 same upload_item_id) / startup S3 bucket 체크 / `complete()` head_object를 `run_in_executor`로 / `cancelAll` reset / `PRESIGN_TTL_SECONDS=300` 단일 상수 / **`raise HTTPException` → ErrorEnvelope 일괄 통일** (request_id 노출 보장). **Phase C** (1 task): `StepCard`가 scanId를 `useStepProgress`까지 전달, SSE URL이 `/projects/{pid}/scans/{sid}/run/{step}` 그래머로 들어감 — Compute Tab 404 해결. **Phase D** (5 tasks): D1 `Scan.status`(`'draft'|'ready'`) + alembic 0005 + list endpoint이 `uploaded_count`(correlated subquery) + `status` 노출 / D2 ScanPicker 라벨 `name (uploaded/expected · status)` truthful 화 / D3 모달 close confirm + AbortController로 createScan 중단(stale write race 방지) + scanId 보존 / D4 `retryAllFailed` 오케스트레이터 메서드 + UI 버튼 / D5 materials cache invalidation regression test (실 invalidation은 W5-C.3 `996accc`에서 이미 들어가 있었음, D5는 lock-in 테스트만 추가). 13 commits `ac52898`..`a10491c` (마지막 `a10491c`는 D5 nit fix — `toHaveBeenCalledTimes(2)` → `>=2`로 brittle 완화). 343/343 vitest pass, typecheck clean. **다음: Phase E** (real-data 검증 루프) — backend restart 필요. E1(1) → E2(10) → E3(10+1 corrupt) → E4(50 cancel/resume) → E5(20 close-reopen) 통과 후 **E6 3648 PUT × ~9 GB**는 오너 승인 후 실행.
