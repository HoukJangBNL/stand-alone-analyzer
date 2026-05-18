# stand-alone-analyzer

Streamlit standalone app for interactive 2D material flake analysis. No DB,
no SSH, no GPU required.

## What it is

A self-contained Streamlit application that takes raw microscope images plus
SAM/Cellpose annotations and runs them through a six-stage analysis pipeline
in the browser. Built on top of [`flake-analysis-core`](https://github.com/HoukJangBNL/flake-analysis-core),
which contains the algorithm-only Python package.

## Inputs

Three on-disk paths (set via the sidebar):

1. `raw_images/` — raw microscope frames
2. `annotations.json` — SAM/Cellpose domain segmentations
3. `analysis_folder/` — output directory (created if missing)

## Pipeline (6 tabs)

1. **Background** — compute median background from raw images
2. **Domain Stats** — compute per-domain RGB stats (compute-only)
3. **Selector** — 5-metric bidirectional filter with 4-pane scatter
4. **Clustering** — manual seed-group GMM with per-cluster thresholds
5. **Domain Proximity** — pair distance + flake construction (union-find)
6. **Explorer** — substrate grid + LOD + Include/Exclude label picker

## Install

```bash
# Sibling dependency (algorithm-only package)
pip install -e ~/projects/flake-analysis-core

# This app
pip install -e .

# Run
streamlit run app/streamlit_app.py
```

## License

MIT — see `LICENSE`.

## Status

Alpha. M0 skeleton — Hello World page only. See `plan_v1.md` (in the Qpress
repo, `.agents/tasks/standalone_flake_tool/`) for the full M0 to M5 milestone
breakdown.
