# User Guide

A walk-through of a typical session.

## 1. Set up paths

In the sidebar, enter 3 paths:
- `raw_images/` — folder of microscope tile PNGs (one per FOV)
- `annotations.json` — COCO format with RLE-encoded segmentation (e.g., SAM2 output)
- `analysis_folder/` — empty directory; outputs will be written here

Click **Reload manifest**.

## 2. Compute tab

Run all 3 sub-sections in order (or click **Run All**):

**1. Background** — generates median background from raw_images. Use seed=0 for reproducibility.

**2. Domain Stats** — per-domain mean RGB and std%. Requires Background.

**3. Domain Proximity** — pair distances (annotations only) + union-find flake construction. Independent of Branch B.

Each step writes to a numbered subdirectory (`01_…`, `02_…`, `05_…`).

## 3. Selector tab

5-metric bidirectional filter (area, std_r/g/b, SAM2). Use **Select All** to pass all domains.

The 4-pane RGB scatter (3D + R-G + R-B + G-B) shows accepted (green) vs rejected (red) domains. Lasso/box select in any 2D pane to highlight domains in all 4 panes (linked brushing).

Click **Commit selection** to write `03_selector/selection.parquet`.

## 4. Clustering tab

Build seed groups manually:
1. Lasso domains in any 2D scatter pane
2. Type a group name → **+ Add**
3. Repeat for at least 2 groups (e.g., "graphite", "thin_layer")

Click **Fit GMM** to fit a Gaussian Mixture Model (`random_state=42`, manual `means_init` from your seed groups).

After fitting, **Per-Cluster Probability Thresholds** sliders let you reject low-confidence assignments without refitting.

Outputs: `04_clustering/{labels.json, assignments.parquet, gmm_model.pkl, seed_groups.json}`.

## 5. Explorer tab

Drag clusters into Include / Exclude using the multiselects. The flake list updates live; click a row to see DetailPanel.

The **substrate grid** (LOD 2) shows pass-ratio per tile; gold border = selected.

Click **Save explorer state** to write `06_explorer/explorer_state.json`.

## Common operations

- **Re-run a step**: just hit its **Compute** button again. Outputs overwrite.
- **Stale detection**: yellow warning in sidebar Pipeline Status means upstream changed. (warn-only — never auto-deletes.)
- **Reproducibility**: same seed + same inputs → same outputs. Verified by `tests/parity/test_reproducibility.py`.

## Troubleshooting

**"Background step not completed"** — run Tab 1 first.
**"Domain Stats step not completed"** — selector requires stats; run Compute → Domain Stats.
**Empty cluster list** — Clustering needs at least 2 seed groups before GMM fit.
**Linked brushing not syncing** — single-pane select only; 3D pane is display-only (Plotly limitation).
