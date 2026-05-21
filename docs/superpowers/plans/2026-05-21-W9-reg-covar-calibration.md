# W9 — `reg_covar` Real-Data Calibration Sweep Implementation Plan

> **Status: SKETCH + DECISIONS-PENDING.** Captures the calibration plan to validate (or refute) the W4.4 default `reg_covar = 10.0` against real annotated projects, and to extend `auto_tune_reg_covar` to co-tune `max_mahalanobis` if leak appears under fog/overlap conditions. PM must resolve §"Decisions Pending" — chiefly which annotated dataset the sweep runs against — before this plan becomes executable.

> **For agentic workers (after sign-off):** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Validate that the W4.4 default `reg_covar = 10.0` and the `auto_tune_reg_covar` driver (single-axis sweep) hold up on realistic flake data — overlapping color distributions, skewed/non-Gaussian per-blob tails, and intercluster "fog" that the synthetic bench at `claudedocs/clustering-tunable-spec.md §6` warned about. If leak rises above 5%, extend `auto_tune_reg_covar` to a 2-axis grid over `(reg_covar, max_mahalanobis)`.

**Why now:** W4.4 closed with the synthetic invariant satisfied (seed-blob 100% recall, leak 0% on the 10-Gaussian bench), but the spec doc explicitly flagged that real microscopy data is not 10 perfectly-Gaussian RGB blobs. Defaults that pass synthetic can underfit (too small `reg_covar` → seed covariance rank-deficient → cluster collapses) or overfit (too large → covariance fattens, neighbours of unseeded blobs leak in). We can't ship the cluster tuner UI (W3.5) to users without knowing the default sits in the right band on real data.

**Architecture (intent, not pinned):**
- **Offline sweep, not in-API.** Calibration is a one-shot lab procedure — runs locally (or on a beefy box), writes a CSV/parquet of results, the human (or PM) reads the table and picks defaults. Not a live endpoint.
- **Reuse existing parity fixture machinery.** `tests/parity/fixture_builder.py` already understands annotated project shapes; the calibration script should consume the same fixtures (or a real annotated project on disk) and never duplicate that logic.
- **Two-stage scoring.** Stage 1: blob-recall + leak rate per `(reg_covar, max_mahalanobis)` cell. Stage 2: human review of edge cells where the metric is ambiguous (recall high, leak slightly above 5%).
- **Output: a calibration report.** `claudedocs/clustering-calibration-2026-05-DD.md` with the chosen defaults, the data they were calibrated against, and a per-cell heatmap. Anyone re-tuning later starts from this report.

**Tech Stack (intent):**
- Sweep driver: pure Python, `numpy`, `pandas` (for the result table), `flake_analysis.core.clustering.engine` + `auto_opt`. No FastAPI, no React.
- Plotting: matplotlib heatmap. Not committed to repo — saved alongside the report under `claudedocs/calibration/`.
- Tests: smoke test that the sweep driver runs end-to-end on a 10-row synthetic fixture (`tests/algo/test_calibration_sweep.py`). The actual calibration run is not a test.

**Pre-read:**
- `src/flake_analysis/core/clustering/auto_opt.py` (`auto_tune_reg_covar` — currently single-axis).
- `src/flake_analysis/core/clustering/engine.py` (`InteractiveClusteringEngine.fit`).
- `claudedocs/clustering-tunable-spec.md` §2c (reg_covar band), §4 (auto-opt metric), §6 (real-data risk callout).
- `tests/parity/fixture_builder.py` + `tests/parity/golden/` (annotated fixture shape).
- `docs/superpowers/plans/2026-05-21-W4.4-clustering-spec.md` (W4.4 baseline assumptions).

---

## Decisions Pending

### D1. Source dataset for the sweep

| Option | Pros | Cons |
|---|---|---|
| **A. Existing annotated parity golden** (whatever is at `tests/parity/golden/`) | Already on disk; reproducible from CI | May not exhibit fog/overlap — was designed to be representative, not adversarial |
| **B. New annotated project the user supplies** | Real-world fidelity | Owner must label it; turnaround = days to weeks |
| **C. Synthetic adversarial bench** (overlapping centers + skewed tails + fog floor) | Fast to build; targets the known failure modes from `clustering-tunable-spec.md §6` | Still synthetic — doesn't cover all real surprises |
| **D. A + C combined** | Covers both validated baseline and known adversarial modes | Two reports / two recommendations |

**Recommendation**: D (combined). Run on the existing golden first (fast confidence check), then on a synthetic adversarial bench. Defer B until A+C reveal an actual gap.

**Open**: A vs B vs C vs D. **Owner**: user (data availability) + algo-engineer (which adversarial axes to bake into C).

### D2. Sweep grid

- 1-axis sweep (current `auto_tune_reg_covar`): `reg_covar ∈ {0.1, 0.3, 1.0, 3.0, 10.0}`, `max_mahalanobis = 3.0` fixed.
- 2-axis sweep (proposed extension): `reg_covar ∈ {0.1, 0.3, 1.0, 3.0, 10.0, 30.0}`, `max_mahalanobis ∈ {2.5, 3.0, 4.0, 5.0, 8.0}` → 30 cells.
- Coarser? Finer?

**Recommendation**: start with the 30-cell 2-axis grid. Cost: 30 fits × ~seconds each = minutes. Fine enough to show whether the 1D sweep was sufficient.

**Open**: grid resolution. **Owner**: algo-engineer.

### D3. Leak threshold and tiebreaker

- Current implicit policy (W4.4): blob-recall first, Mahalanobis margin tiebreaker. No explicit leak ceiling.
- Spec recommendation (`clustering-tunable-spec.md §4`): blob-recall subject to **leak ≤ 0.05**.
- Should the live `auto_tune_reg_covar` enforce a leak ceiling, or only the offline calibration?

**Recommendation**: enforce in offline calibration (so the picked defaults respect it). Leave live driver as-is for v1 — adding leak computation in the live path means computing labels on unseeded blobs, which requires per-blob ground truth that the live path doesn't have.

**Open**: D3a (online leak enforcement yes/no), D3b (threshold value if D3a=yes). **Owner**: algo-engineer + user.

### D4. Outcome decision tree

What action does each outcome trigger?

| Sweep result | Action |
|---|---|
| Default `(10.0, 3.0)` is the cell winner on both A and C | Status quo. Close W9 with "default validated" report. |
| Default sub-optimal but inside top-3 cells | Update default to the winning cell. Bump driver default in `auto_tune_reg_covar`. |
| Default leaks (>5%) on adversarial C | Extend `auto_tune_reg_covar` to 2-axis (the proposed feature). Re-run. |
| No cell satisfies recall ≥ 0.7 ∧ leak ≤ 0.05 | Escalate: clustering spec needs revisiting (this is a research finding, not a tuning failure). |

**Open**: are these actions auto-executed (subagent dispatched) or do they require explicit user sign-off per case? **Recommendation**: report → user reads → user approves the chosen action. Defaults changes are user-visible behaviour; PM does not auto-merge. **Owner**: user.

### D5. Frequency / re-run policy

- One-shot W9 closes the question for now.
- Or: bake into CI as a slow nightly job that re-runs whenever the parity golden or `auto_tune_reg_covar` changes?

**Recommendation**: one-shot. CI nightly is overkill for a tuning constant that changes per W-series, not per commit.

**Open**: one-shot vs scheduled. **Owner**: user.

---

## Sketch of File Structure (subject to D1–D5)

**New:**
- `scripts/calibration/sweep_reg_covar.py` — driver that loads a project (golden or annotated), runs the 30-cell sweep, writes results + heatmap.
- `scripts/calibration/build_adversarial_bench.py` (D1=C/D) — generates the overlapping/skewed/fog synthetic dataset.
- `claudedocs/calibration/2026-05-DD-reg-covar-sweep.md` — written report.
- `claudedocs/calibration/2026-05-DD-heatmap.png` — saved heatmap (NOT committed unless user requests; .gitignore add).
- `tests/algo/test_calibration_sweep.py` — smoke test on a 10-row fixture.

**Modified (only if D4 says "extend driver"):**
- `src/flake_analysis/core/clustering/auto_opt.py` — extend `auto_tune_reg_covar` to take optional `mahalanobis_candidates` and sweep 2D. Backwards compatible default = current 1D behaviour.
- `src/flake_analysis/api/schemas/clustering.py` + `src/flake_analysis/api/routes/clustering.py` — surface the 2D grid as an opt-in API param if the user wants it tunable from the W3.5 UI.

**No DB schema change.** Calibration is a code/data exercise, not a persistence one.

---

## Tasks (sketched, not executable)

After D1–D5 land, the rewrite turns these into concrete TDD steps.

1. **algo-engineer**: build the 2D sweep helper (`extend auto_opt.auto_tune_reg_covar` to accept optional `mahalanobis_candidates` arg, default `None` = preserve 1D path).
2. **algo-engineer**: write `scripts/calibration/sweep_reg_covar.py` that loads a project's `repr_rgbs` + seeds, calls the 2D helper, writes a `(reg_covar, max_mahalanobis, recall, leak, margin)` parquet. Include a `--dataset` flag (parity golden vs. adversarial vs. user-supplied path).
3. **algo-engineer (D1=C/D only)**: build `scripts/calibration/build_adversarial_bench.py` with three axes — overlapping centers, skewed per-blob tails, fog floor.
4. **algo-engineer**: smoke-test the sweep driver in `tests/algo/test_calibration_sweep.py` (3-cell grid on a 30-row fixture, runs in <1s).
5. **algo-engineer**: run the actual sweep against D1's chosen dataset(s). Save results to `claudedocs/calibration/`.
6. **PM**: write the calibration report (`claudedocs/calibration/2026-05-DD-reg-covar-sweep.md`) summarizing methodology, results, and the recommended action per D4 outcome tree. PM holds the pen because the report's audience is the user.
7. **PM**: route to user for sign-off on the action (status quo / default change / driver extension / spec escalation).
8. **algo-engineer (only if D4 says so)**: ship the action — bump default, extend driver, or escalate.
9. **PM**: update `docs/project-status.md` §3.2 — strike "W9 reg_covar calibration sweep" with a link to the report.

---

## Risk register

- **R1. Calibration overfits to one project.** A single annotated dataset gives one defaults answer; another project might want different defaults. Mitigation: report explicitly states which dataset the recommendation is tuned to. The W3.5 slider exists exactly for this — per-project override.
- **R2. Adversarial synthetic bench mis-models reality.** If we hand-pick fog/overlap parameters that aren't representative, the calibration validates the wrong failure mode. Mitigation: D1=B (real annotated data) is the gold standard; D1=C is a fallback when B is unavailable.
- **R3. Compute cost on large projects.** A 30-cell × N-domain sweep is `O(30 × N)` GMM fits. On a 100K-domain project (`db-schema-v6.md` scale target) this could be 30 × O(N²) → minutes-to-hours. Mitigation: sweep on a stratified subsample (10K domains) when N is large; document the subsample in the report.
- **R4. `auto_tune_reg_covar` 2D extension breaks API contract.** If we change the live driver's default behaviour (not just add an optional arg), the W4.4 parity golden will need regeneration AGAIN — explicit user approval required (per `CLAUDE.md` parity rules). Mitigation: keep 2D opt-in. Default = 1D = current behaviour.
- **R5. Leak metric requires unseeded ground truth.** Computing leak needs to know which domains are NOT seed members. Live path has seeds, doesn't have "everything else is ground-truth-unseeded" — that assumption only holds in calibration where the dataset is fully annotated. Mitigation: keep leak in the offline path only (D3 recommendation).

---

## Next step (PM action)

1. PM raises D1 (dataset source) with the user — single AskUserQuestion. D2/D3/D4/D5 can be defaulted by algo-engineer + reviewed in the rewrite step.
2. Once D1 lands, PM rewrites this file with task-level red→green steps, dispatch to algo-engineer.
3. Final dispatch order: algo-engineer (driver + script + run) → PM (report + route to user) → algo-engineer (action, only if user signs off).

---

## Execution Handoff

**Status: NOT READY.** Decisions D1–D5 must land before this plan becomes executable. D1 is the only true blocker — the rest can be defaulted by the algo-engineer in the rewrite step and reviewed during PR.
