# Conventions

What we observed in Olostep's public Python repos before writing this project,
and how this project follows (or knowingly departs from) them.

Repos read (cloned 2026-11-25): `olostep-api/pdf-scraper`,
`olostep-api/batch-web-scraper`, `olostep-api/email-finder`,
`olostep-api/amazon-price-tracker`, and the SDK `olostep-api/olostep-py`
(plus the installed `olostep` 1.2.3 package).

## Observed

| Area | What the repos do |
| --- | --- |
| Layout | `main.py` (CLI) and `app.py` (UI) at the root; code in `src/`; helpers in `utils/`; settings in `config/` (`config.py` or `settings.py`). |
| Config | Module-level UPPER_CASE constants plus a frozen `@dataclass` (`Config` / `Settings`) built by a loader (`load_config()`, `Settings.from_env()`). |
| Secrets | `OLOSTEP_API_KEY` in `.env`, with a committed `.env.example`. Missing key -> clear error (`raise SystemExit("Missing OLOSTEP_API_KEY env var.")` in pdf-scraper, `ValueError` with a hint in email-finder). |
| CLI | `argparse` with flat flags (`--url`, `--urls-file`, `--out`, `--formats`, `--poll-seconds`). No sub-commands. |
| Async | `asyncio` throughout; `httpx.AsyncClient` for API calls. |
| Olostep calls | Raw REST with `httpx` (`/v1/batches`, `/v1/batches/{id}/items`, `/v1/retrieve`), not the SDK. |
| Logging | `loguru` in pdf-scraper and batch-web-scraper; stdlib `logging` with a `setup_logging()` helper (and httpx/httpcore muted to WARNING) in email-finder. |
| Types | `from __future__ import annotations`, type hints on public functions, frozen dataclasses for requests/results. Docstrings on main classes and methods. |
| UI | Streamlit (`app.py`) in pdf-scraper, batch-web-scraper and amazon-price-tracker (Gradio in email-finder). pdf-scraper injects CSS with `--primary: #635bff`. A `ui.png` screenshot in the README. |
| README | Title, short pitch, screenshot, Features / Requirements / Quick Start (numbered 1-2-3) / Configuration / Usage / CLI Reference (flag table) / Output Layout / Project Structure (tree with comments) / Olostep References / License. |
| Licence | MIT `LICENSE` in pdf-scraper; none in the other three. |
| Tooling | `requirements.txt` only. No `pyproject.toml`, ruff, black or pre-commit config in the four app repos. The SDK uses `pyproject.toml` (Poetry) and pytest. |
| Tests | pdf-scraper lists a `test/` folder in its README but ignores it in `.gitignore`; no tests are committed in the app repos. |

## How this project follows them

- `app.py` at the root, Streamlit UI, `.env` + `.env.example` with `OLOSTEP_API_KEY`.
- UPPER_CASE constants and frozen dataclasses for settings, all in `config.py`.
- `argparse`, `asyncio`, `from __future__ import annotations`, type hints and
  docstrings everywhere.
- stdlib `logging` with a `setup_logging()` helper that mutes httpx/httpcore,
  like email-finder.
- README sections follow pdf-scraper's order, including a CLI table, output
  layout, project structure tree and Olostep references.
- MIT licence.
- Official Olostep logo in the UI header (pdf-scraper uses `#635bff` as its primary).

## Where we depart, and why

| Departure | Reason |
| --- | --- |
| Official `olostep` SDK instead of raw `httpx` calls | Required for this project. `httpx` is used only for GOV.UK. |
| `src/olostep_pdf_batch/` package with `python -m` and sub-commands | Five separate steps (collect, run, resume, report, extract) read better as sub-commands; matches the companion `olostep-batch-scraper` project. |
| `pyproject.toml` + ruff + pytest, with committed tests | Required for this project; the SDK repo also uses `pyproject.toml` and pytest. |
| stdlib `logging` + `rich`, not `loguru` | Required; also matches email-finder. |
| No sidebar: settings live inside the tool window | Matches the tool pages on olostep.com, which keep inputs and actions in one window. |
| UI code in `ui/`, pipeline code in the package | Keeps all logic shared between CLI and UI (`pipeline.py`). |
| UI palette measured from olostep.com (primary `#1369E6`, the site's button blue), not pdf-scraper's `#635bff` and hand-picked tints | pdf-scraper's extra tints and gradients (`#f8f9fc`, `#10142a`, ...) do not appear on olostep.com. See `ui/theme.py`. |
