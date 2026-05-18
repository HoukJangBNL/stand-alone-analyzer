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
