# Parity / End-to-End Validation Harness (M3)

This directory contains tests that exercise the full standalone pipeline
on a synthetic fixture, verifying:

1. All 6 pipeline steps produce expected output files (and none are 0-byte).
2. `labels.json` conforms to the frozen schema v1 (plan §7.1).
3. Reproducibility: same seed → same outputs (background byte-equal,
   clustering n_clusters + assignments equal).
4. `manifest.json` has all step entries with `completed_at` and
   `params_hash` set.
5. Parquet column schemas match expectations (selector, clustering
   assignments, flake assignments).
6. **Numerical regression** — every output value matches a frozen
   golden record so silent drift in `flake_analysis.core` cannot
   sneak through (see `golden/` and `test_golden_regression.py`).

## Run

```bash
cd ~/projects/stand-alone-analyzer
source .venv/bin/activate
pytest tests/parity/ -v
```

## Files

| File | Purpose |
|---|---|
| `fixture_builder.py` | `build_fixture()` — produces 5 PNGs + 10 RLE blobs under `<dir>/raw_images/` and `<dir>/segmentation/annotations.json`. |
| `test_pipeline_e2e.py` | Runs all 6 steps end-to-end; verifies outputs, manifest, labels schema. |
| `test_reproducibility.py` | Two independent runs → byte-equal background, identical clustering. |
| `test_schema_validators.py` | Module-scoped completed fixture; validates manifest + parquet + labels schemas. |
| `test_golden_regression.py` | Numerical regression test — every output compared to frozen `golden/pipeline_golden.json`. |
| `regenerate_golden.py` | CLI to (re)generate `golden/pipeline_golden.json`. Run only when the algorithm changed intentionally and the new baseline has been signed off. |
| `golden/pipeline_golden.json` | Frozen authoritative outputs from commit `e2a2ede` (2026-05-21). See "Golden fixtures" below. |

## Notes (post-r8)

After plan v1 r8, Qpress is **not** modified, so a true Qpress↔standalone
diff is not part of M3. Future work could add that as an opt-in test if a
Qpress reference run is available, but for v0.1 we only validate
standalone-internal consistency.

The fixture is deliberately small (5 images @ 200×200, 10 domains); the
entire `tests/parity/` suite completes in ~2 seconds.

## Fixture color design

The four blob-color centers are well-separated to give the GMM real
variance to fit (without this, a 2-component GMM on near-identical RGBs
collapses or returns trivial assignments). See `_BLOB_COLORS` in
`fixture_builder.py` and the smoke run that confirms `n_clusters=2` for
the canonical fixture.

## Golden fixtures

`golden/pipeline_golden.json` is the **frozen numerical baseline** for
the canonical fixture (`build_fixture(n_images=5, image_size=200,
seed=0)`).

### What's inside

| Output | Storage | Why |
|---|---|---|
| `01_background/background.npy` | SHA-256 + summary stats | Large array (~960 KB); SHA-256 is the gate, summary stats help diagnose drift magnitude. |
| `02_domain_stats/stats.npz` | Per-key full arrays | Small (1.9 KB); stored as flat lists for human-diffability. |
| `03_selector/selection.parquet` | Per-column data + dtypes | Robust to pyarrow binary-format changes. |
| `04_clustering/labels.json` | Full JSON, `fitted_at` stripped | `fitted_at` is wall-clock UTC and varies every run by design. |
| `04_clustering/assignments.parquet` | Per-column data + dtypes | Same rationale as `selection.parquet`. |
| `04_clustering/gmm_model.pkl` | SHA-256 only | Opaque pickle; byte-stable across runs given seeded fit. |
| `05_domain_proximity/distances.parquet` | Per-column data + dtypes | Same rationale. |
| `05_domain_proximity/flake_assignments.parquet` | Per-column data + dtypes | Same rationale. |

Total size: ~9 KB.

Frozen at commit `e2a2ede` on 2026-05-21.

### Volatile fields

* `labels.json["fitted_at"]` — stripped from both the golden record
  and the live comparison. Listed in `LABELS_VOLATILE_FIELDS` in
  `regenerate_golden.py`.
* `manifest.json` is not in the golden record at all — every step
  entry has its own per-run `completed_at` timestamp. Manifest
  schema is asserted by `test_schema_validators.py` instead.

### Tolerances

`test_golden_regression.py` uses `rtol=1e-10, atol=1e-12` for float
comparisons — much tighter than the algo domain rule's default
(`rtol=1e-5, atol=1e-8`). Rationale: this test runs the same code on
the same deterministic fixture, so output should be reproducible to
near machine precision. The narrow tolerance is what lets the harness
catch subtle drift.

### Regeneration (intentional algorithm change)

If a code change intentionally shifts the numerical output, the
golden file must be regenerated:

```bash
cd ~/projects/stand-alone-analyzer
source .venv/bin/activate
python -m tests.parity.regenerate_golden
pytest tests/parity/test_golden_regression.py -v   # should pass after regen
```

**Do not regenerate without explicit user sign-off** — the whole
point of the golden record is to make numerical changes loud. If
this test fails on your branch, first investigate *why*; only
regenerate after confirming the new values are correct.

When regenerating, also note the commit ref + date in this README
table so future maintainers can trace which behaviour the golden
encodes.
