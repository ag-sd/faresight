# Faresight — Claude Context

## Project summary

Local expense tracker. FastAPI backend + SQLite (SQLAlchemy 2) + plain HTML/JS
frontend with Chart.js. No NAS sync yet — that is a planned future step.

## Original scaffold instruction

> Build a local expense tracker web app using FastAPI for the backend and
> SQLite for storage (via SQLAlchemy), with a plain HTML/JS frontend using
> Chart.js for charts.
>
> Project structure:
> - app/faresight.py — FastAPI app
> - app/database.py — SQLAlchemy engine pointing to a LOCAL SQLite file path
>   defined in config
> - app/models.py — Transaction model (id, date, description, amount,
>   category, note, source, created_at)
> - app/nas.py — placeholder module for NAS sync logic (implement in a
>   later step)
> - frontend/ — HTML/JS/Chart.js dashboard
> - config.yaml — app configuration file
>
> config.yaml should include:
>   nas_share_path: /mnt/nas-expenses/expenses.db
>   local_db_path: ~/.local/share/expense-tracker/local.db
>   sync_on_startup: true
>   sync_on_shutdown: true
>   sync_interval_minutes: 5
>
> Scaffold the project, install dependencies, and get a basic version
> running on localhost:8000 reading from local_db_path. Don't implement
> the NAS sync logic yet — just get the skeleton running against the
> local SQLite file.

## Key files

- `app/config.py` — loads `config.yaml`; all other modules import constants from here
- `app/database.py` — creates the SQLAlchemy engine from `LOCAL_DB_PATH`; auto-creates the parent directory
- `app/models.py` — `Transaction` table
- `app/schemas.py` — Pydantic schemas for request/response
- `app/faresight.py` — all routes; serves `frontend/index.html` at `/`
- `app/nas.py` — stub only; raises `NotImplementedError`
- `frontend/index.html` — single-file dashboard

## Development rules

- **Always add tests.** Every code change — new feature, bug fix, refactor — must include
  corresponding tests in `tests/`. Run `pytest` before declaring work done.
- Tests use an in-memory SQLite DB via the `client` fixture in `tests/conftest.py`.
  Never write tests that touch the real `local_db_path`.

## Runtime notes

- Python 3.14 is in use. Use `Optional[T]` from `typing` instead of `T | None`
  in Pydantic models and FastAPI route signatures — the `X | None` union syntax
  triggers a Pydantic evaluation bug on Python 3.14.
- Virtualenv lives at `.venv/`; activate with `source .venv/bin/activate`
- Run: `uvicorn app.faresight:app --reload`
- DB is created automatically at `~/.local/share/expense-tracker/local.db`

## What is NOT implemented yet

- `app/nas.py` — NAS sync (copy DB to/from NAS share defined in `config.yaml`)
- Background sync scheduler
- CSV import
- Date-range filtering on the transactions endpoint
