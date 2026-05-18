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
- Streamlit ≥1.32, Plotly ≥5.18
- Logging via `logging` (stdlib) — never `print()`
- Type hints on all public functions
- Docstrings on all modules + public functions
- 100-character line limit (informal)

## Tests

- All new code MUST include tests under `tests/`
- Streamlit smoke tests: import the page module and call `render()` with a stubbed session_state
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
- Streamlit version
- Minimal reproduction (paths used, raw_images/annotations sample if possible)
- Expected vs actual behavior
- Browser console errors (if Plotly-related)
