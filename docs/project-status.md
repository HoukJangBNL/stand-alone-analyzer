# Project Status

> **Living document.** PM이 작업 종료 시점마다 갱신.
> 사용자(오너)가 "지금 어디까지 와 있나" 30초 안에 파악할 수 있어야 함.
> 디테일 디자인·DDL·운영 절차는 별도 문서로 링크 — 여기에는 **상태**만.

**Last updated**: 2026-05-21
**Current branch**: `feat/migration-cutover` (post-cutover punch list 진행 중)
**Schema version**: `v6` (alembic `0001_initial_v6` applied to RDS `qpress`)

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

---

## 3. 진행 중 / 다음 작업

### 3.1 다음 한 발
**API ↔ DB 통합 시작.** `get_db()` async dependency 도입 → `routes/projects.py`부터 in-memory 저장소를 ORM 호출로 교체.

### 3.2 백로그 (우선순위 순)

- [ ] FastAPI `get_db()` dependency + 첫 라우트(예: `routes/projects.py`) DB 통합
  - **W2.1 후속 (W4.2 deferred)**: `GET /projects/{pid}/data/manifest`가 아직 on-disk `manifest.json`에서 읽음. W2.1에서 `ProjectContext`로 `project_id → analysis_id` 해석되면 `db.reconcile.derive_manifest_steps_from_analysis`로 rewire.
- [ ] 업로드 플로우 — S3 presigned URL + `upload_sessions` / `upload_items` 라이프사이클
- [ ] 인증/세션 (현재 system user만 사용, 실제 user 도입 시점 결정 필요)
- [ ] React 프론트엔드 (`web/`) — 인증/프로젝트 리스트/업로드 UI
- [ ] GPU 워커 트리거 (background → SAM → domain_stats → domain_proximity), `runs` audit log 활용
- [ ] CI: alembic offline render + 모델↔스키마 drift check
- [ ] SSM Session Manager 검토 (SSH key 배포 없이 DB 접근)

---

## 4. 미해결 결정 / 사용자 승인 대기

- (없음)

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
