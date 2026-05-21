# Changelog

## v0.3.0 — 2026-05-21 — React + FastAPI cutover

This is the cutover release. The Streamlit UI is removed; the React +
FastAPI stack is the only supported runtime.

### Removed
- `app/streamlit_app.py` entrypoint.
- `src/flake_analysis/ui/` (sidebar, brushing, image_preview, tab_compute, tab_selector, tab_clustering, tab_explorer).
- `streamlit>=1.32` and `plotly>=5.18` from `pyproject.toml`.
- Streamlit-only tests: `test_brushing.py`, `test_image_preview.py`, `test_explorer_mosaic_helpers.py`, `test_selector_filter_persistence.py`.

### Added
- `deploy/nginx/stand-alone-analyzer.conf` — nginx server config (verbatim port of deployment-design §2.1).
- `deploy/systemd/saa-api.service` — systemd unit (Restart=on-failure, KillMode=mixed, User=<EDIT-ME>).
- `deploy/scripts/deploy.sh` — atomic symlink-rotation deploy script.
- `docs/operations/runbook.md` — install / restart / log / rollback recipes.
- Cutover guard tests: `tests/test_no_streamlit.py`, `tests/test_pyproject_clean.py`.
- Deploy-artifact shape tests: `tests/test_nginx_config_syntax.py`, `tests/test_systemd_unit.py`, `tests/test_deploy_script.py`.
- `tests/test_xaccel_thumbnails.py` — verifies Phase 3 X-Accel-Redirect conversion.

### Changed
- `pyproject.toml` `description` — "Streamlit app for…" → "React + FastAPI app for…".
- `pyproject.toml` `version` — `0.2.18` → `0.3.0`.
- `src/flake_analysis/__init__.py` — `__version__` bumped, docstring rewritten.
- `src/flake_analysis/api/routes/static.py` — thumbnail route now emits `X-Accel-Redirect` when `00_thumbnails/index.json["cache_dir"]` is present (deployment-design §2.2 Option B); legacy in-folder layouts keep the `FileResponse` fallback.
- `tests/test_imports.py` — dropped `flake_analysis.ui` import; relaxed version assertion.
- `tests/test_pipeline_selector.py` — inlined `_values_for_axis` helper.
- `README.md` — Quick-start rewritten for `npm run build` + uvicorn.
- `CONTRIBUTING.md` — Streamlit dev-loop dropped; React + FastAPI dev-loop added.

### Rollback
`git revert` the cutover PR. There is no on-host parallel-run; the Streamlit code is gone.
