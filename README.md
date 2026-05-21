# stand-alone-analyzer

A React + FastAPI app for interactive 2D material flake analysis.

Loads pre-computed segmentation masks (COCO + RLE) and provides a 4-tab pipeline GUI for background generation, color analysis, manual clustering, and label-based filtering.

The algorithm core (`flake_analysis.core`) was previously published as a
separate package, [`flake-analysis-core`](https://github.com/HoukJangBNL/flake-analysis-core).
It was merged into this repo in v0.2.0 to simplify install (single clone,
single venv).

## Status

`v0.3.0` — beta. React + FastAPI cutover complete; AWS RDS + alembic schema (v6) bootstrapped. Currently in transition between two operating modes:

- **Local mode** (default, what most users get): Single-user desktop tool. Reads/writes `analysis_folder/` on local disk. No DB connection required.
- **DB mode** (in progress): Postgres on AWS RDS via SSH tunnel, alembic-managed schema. Used by the team for the Qpress integration path. See [`docs/project-status.md`](docs/project-status.md) for the current migration state.

The two modes share the same FastAPI backend — DB mode adds persistence; it does not replace the filesystem layout.

## Quick start

```bash
# Clone and install (Python deps for the FastAPI backend)
git clone https://github.com/HoukJangBNL/stand-alone-analyzer.git
cd stand-alone-analyzer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Build the React frontend
cd web
npm install
npm run build
cd ..

# Run the FastAPI backend (serves /api/v1/*)
uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000
```

For production, see `docs/operations/runbook.md` (nginx + systemd
serve `web/dist/` + reverse-proxy uvicorn). For development with
hot-reload, see `CONTRIBUTING.md` (`npm run dev` on :5173 + uvicorn
`--reload` on :8000).

Open the SPA at http://127.0.0.1:8000/ (or http://localhost:5173/ in dev). The 3 input paths are wired through the FastAPI backend:

1. **raw_images/** — folder of microscope tile PNGs
2. **annotations.json** — COCO+RLE segmentation output (e.g., from SAM2)
3. **analysis_folder/** — empty directory (will be populated)

> **Current limitation**: the project-creation sidebar UI is not yet implemented — the SPA defaults to a project ID of `local`, and the 3 paths must be set via API (`POST /api/v1/projects`) or via environment / manifest until the sidebar lands. Track progress in [`docs/project-status.md`](docs/project-status.md).

## Pipeline tabs

| # | Tab | What it does |
|---|---|---|
| 1 | Compute | Background → Domain Stats → Domain Proximity (3 expanders) |
| 2 | Selector | 5-metric (area / std / SAM2) bidirectional filter + 4-pane RGB scatter with linked brushing |
| 3 | Clustering | Manual seed-group GMM with per-cluster posterior thresholds |
| 4 | Explorer | Substrate-grid LOD 2 + Include/Exclude label picker + 3-pane Z-layout (canvas + flake list + DetailPanel) |

See [`docs/project-status.md`](docs/project-status.md) for current state and [`docs/db-schema-v6.md`](docs/db-schema-v6.md) for the DB schema.

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

## Database setup (DB mode only)

> Skip this section if you're using local mode — no DB is required for the desktop workflow.

Schema is managed by Alembic against a Postgres RDS instance reached via SSH tunnel. Full operational procedure (bastion start/stop, SG rules, secret retrieval) lives in [`docs/db-ops.md`](docs/db-ops.md).

Prerequisites: SSH tunnel forwarding `localhost:5432 -> RDS:5432` is active (`docs/db-ops.md` §2.4).

```bash
export SAA_DB_HOST=localhost
export SAA_DB_PORT=5432
export SAA_DB_USER=houk
export SAA_DB_PASSWORD=...        # from Secrets Manager (db-ops.md §2.5)
export SAA_DB_NAME=qpress

alembic upgrade head              # apply pending migrations
alembic current                   # show current revision
alembic history                   # list all revisions
```

The schema source of truth is [`docs/db-schema-v6.md`](docs/db-schema-v6.md). Do not use `alembic revision --autogenerate` — see runbook §3 for why.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Adapted from the Qpress analyzer module (BNL/CFN).
