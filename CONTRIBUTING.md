# Contributing to stand-alone-analyzer

Thanks for considering a contribution!

## Development setup

This app depends on a sibling checkout of [flake-analysis-core](https://github.com/HoukJangBNL/flake-analysis-core). Clone both repos side by side:

```bash
git clone https://github.com/HoukJangBNL/flake-analysis-core.git
git clone https://github.com/HoukJangBNL/stand-alone-analyzer.git
cd stand-alone-analyzer
python -m venv .venv && source .venv/bin/activate
pip install -e ../flake-analysis-core
pip install -e ".[dev]"
pytest -v
```

## Code conventions

- Python 3.10+
- React 18.3, FastAPI ≥0.110 (frontend deps live in web/package.json; backend deps in pyproject.toml)
- Logging via `logging` (stdlib) — never `print()`
- Type hints on all public functions
- Docstrings on all modules + public functions
- 100-character line limit (informal)

## Tests

- All new code MUST include tests under `tests/`
- Frontend tests: vitest under web/src/**/__tests__/ — run via cd web && npx vitest run
- Parity tests live in `tests/parity/` — keep them fast (<10s each)
- Run `pytest -v` before pushing
- Full suite must complete in <60 seconds

## Pull requests

- Branch from `main`
- One logical change per PR
- Include test coverage for new code
- Squash merge preferred

## Reporting issues

GitHub Issues — please include:
- Python version + OS
- Browser + version (frontend) and FastAPI/uvicorn version (backend)
- Minimal reproduction (paths used, raw_images/annotations sample if possible)
- Expected vs actual behavior
- Browser console errors

## Local dev loop (Plan 5 cutover, v0.3.0+)

Two terminals, one for the React dev server and one for uvicorn with
hot-reload. Vite proxies `/api/*` to `127.0.0.1:8000` so the same
URL shape works in dev and prod (deployment-design §8.1).

```bash
# Terminal 1 — React HMR on :5173
cd web && npm run dev

# Terminal 2 — FastAPI with reload on :8000
SAA_LOG_LEVEL=debug uvicorn flake_analysis.api.main:app --reload --port 8000
```

Open http://localhost:5173/ in a browser. Backend changes auto-reload
via uvicorn `--reload`; frontend changes hot-reload via Vite.
