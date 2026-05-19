# stand-alone-analyzer

A Streamlit app for interactive 2D material flake analysis.

Loads pre-computed segmentation masks (COCO + RLE) and provides a 4-tab pipeline GUI for background generation, color analysis, manual clustering, and label-based filtering.

The algorithm core (`flake_analysis.core`) was previously published as a
separate package, [`flake-analysis-core`](https://github.com/HoukJangBNL/flake-analysis-core).
It was merged into this repo in v0.2.0 to simplify install (single clone,
single venv).

## Status

`v0.2.0` — beta. Single-user desktop tool. No DB, no SSH, no GPU.

## Quick start

```bash
# Clone and install
git clone https://github.com/HoukJangBNL/stand-alone-analyzer.git
cd stand-alone-analyzer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run
streamlit run app/streamlit_app.py
```

Then enter 3 paths in the sidebar:
1. **raw_images/** — folder of microscope tile PNGs
2. **annotations.json** — COCO+RLE segmentation output (e.g., from SAM2)
3. **analysis_folder/** — empty directory (will be populated)

## Pipeline tabs

| # | Tab | What it does |
|---|---|---|
| 1 | Compute | Background → Domain Stats → Domain Proximity (3 expanders) |
| 2 | Selector | 5-metric (area / std / SAM2) bidirectional filter + 4-pane RGB scatter with linked brushing |
| 3 | Clustering | Manual seed-group GMM with per-cluster posterior thresholds |
| 4 | Explorer | Substrate-grid LOD 2 + Include/Exclude label picker + 3-pane Z-layout (canvas + flake list + DetailPanel) |

See [USER_GUIDE.md](docs/USER_GUIDE.md) for detailed workflow.

## Filesystem layout

After a complete run, your `analysis_folder/` will look like:

```
analysis/
  manifest.json
  01_background/background.npy
  02_domain_stats/stats.npz
  03_selector/selection.parquet
  04_clustering/{labels.json, gmm_model.pkl, assignments.parquet, seed_groups.json}
  05_domain_proximity/{distances.parquet, flake_assignments.parquet}
  06_explorer/{explorer_state.json, selected_flakes.parquet}
```

## Tests

```bash
pytest -v                    # full suite (~44 tests)
pytest tests/parity/ -v      # M3 end-to-end parity harness
```

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Adapted from the Qpress analyzer module (BNL/CFN).
