# Project Status

> **Living document.** PM이 작업 종료 시점마다 갱신.
> 사용자(오너)가 "지금 어디까지 와 있나" 30초 안에 파악할 수 있어야 함.
> 디테일 디자인·DDL·운영 절차는 별도 문서로 링크 — 여기에는 **상태**만.

**Last updated**: 2026-05-21
**Current branch**: `feat/migration-cutover` (post-cutover punch list 진행 중 — W3.5 / W2.4 / W8.1 / **W6 (auth+ACL+usage)** 완료. W6.1 Cognito 부트스트랩만 AWS 승인 대기. 다음은 W5 / W7 / W8.2 / W9의 D-block 결정)
**Schema version**: `v7` (alembic `0002_v7_auth` 헤드 — UUID users + ENUMs + project_users + usage_events. DB 적용은 W6.1 Cognito 승인 후 함께)

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
**W6.1 Cognito 부트스트랩 (AWS 승인 대기)** + 남은 SKETCH 플랜 D-block 결정. W6 코드는 모두 랜드(28 commits). DB 마이그레이션 적용 + Cognito User Pool 생성 + SSM 파라미터 등록은 사용자 승인 떨어지면 `scripts/devops/cognito_bootstrap.sh` 실행 한 번으로 완료. 그 후 남은 sketch는 W5(업로드) / W7(GPU) / W8 Part 2(SSM) / W9(reg_covar 캘리브레이션) — 우선순위는 사용자 결정.

### 3.2 백로그 (우선순위 순)

> 모든 백로그 항목에 대해 플랜 작성 완료. 실행 가능 여부는 각 플랜의 "Execution Handoff" 섹션을 참고.

- [x] **W3.5** 인터랙티브 클러스터링 튜너 UI — [`plan`](superpowers/plans/2026-05-21-W3.5-clustering-tuner.md) — backend slice (`reg_covar` + `auto_tune` schema/route + SSE `reg_covar_chosen`), frontend slice (log-scale slider [0.1, 10.0] + AutoTune 버튼 + ClusteringRightRail 마운트). 9 commits (`e082d90`..`ab87bf9`). 246 vitest tests pass.
- [x] **W2.4** manifest 엔드포인트 DB rewire — [`plan`](superpowers/plans/2026-05-21-W2.4-manifest-db-rewire.md) — `GET /data/manifest`가 DB의 `analyses.steps_done`를 source of truth로 오버레이 (background/sam/domain_stats/domain_proximity + 신규 top-level `status`). DB row 없으면 silent fallback, SQL error는 500 `db_unavailable`. 완료 — `2d5d7d5`.
- [ ] **W5** 업로드 플로우 (S3 presigned URL + `upload_sessions`/`upload_items`) — [`plan (SKETCH)`](superpowers/plans/2026-05-21-W5-upload-flow.md) — D1–D6 결정 필요(버킷 레이아웃·SHA256 strategy·세션 경계·메타데이터 소스·인증 순서·UI 라우트).
- [x] **W6** 인증/세션 (Cognito + 4-tier 글로벌 ENUM + per-project ACL + usage events + 프론트엔드 슬라이스) — [`plan v2`](superpowers/plans/2026-05-21-W6-auth-session-v2.md) — 6 sub-plan 완료(W6.0 schema v7 / W6.1 Cognito 인프라 docs / W6.2 백엔드 의존성 / W6.3 ACL / W6.4 usage events / W6.5 프론트엔드). 28 commits (`8bf3c90`..`bd9339b`). 백엔드 비-PG 테스트 292 pass + 프론트엔드 vitest 265 pass. **W6.1 Cognito 부트스트랩 실행은 AWS 승인 대기.**
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
- 2026-05-21: **W6 전체** 완료 (코드, 28 commits) — Cognito + 4-tier 글로벌 ENUM(member/reader/operator/admin) + per-project ACL(viewer/editor) + usage events + 프론트엔드. 6 sub-plan: **W6.0** schema v7 (`8bf3c90`..`c51fba0`, UUID users + ENUMs + project_users + usage_events + alembic `0002_v7_auth`), **W6.1** Cognito 인프라 docs (`ad645cf` / `8dd41ea`, 부트스트랩/스모크 스크립트 + 런북 — AWS 실행은 승인 대기), **W6.2** 백엔드 의존성 (`fefd45e`..`f4aeb16`, JWKS verifier + cognito_sub upsert + dev-bypass + /auth/me·callback·logout), **W6.3** ACL (`57445f5`..`7c75ece`, 순수 resolver + require_role/require_project_role 가드 + admin 라우트), **W6.4** usage events (`33d1c5f`..`f53f36f`, emit 헬퍼 + login/logout/scan_run 훅 + GET /admin/usage), **W6.5** 프론트엔드 (`0518258`..`bd9339b`, authSlice + LoginPage/SignupPage/AdminPage + RequireAuth/RequireRole + 사이드바 + 모든 fetch에 Bearer 토큰). 백엔드 비-PG 292 pass, vitest 265 pass. 다음: W6.1 Cognito 부트스트랩 AWS 승인.
