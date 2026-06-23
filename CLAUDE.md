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

## NAS sync (`app/sync.py`)

`sync_from_nas()` runs once at startup (inside the FastAPI lifespan, before requests).
It is synchronous — no threads, no scheduler.

Decision tree:
1. NAS dir unreachable → warn, continue offline; `_status["reachable"] = False`
2. NAS file absent → push local DB up (first run); `last_action = "pushed_initial"`
3. NAS mtime > marker → backup local to `.db.bak`, pull NAS down, update marker; `last_action = "pulled_update"`
4. Local is current → skip; `last_action = "skipped_current"`

Marker file: `local_db_path + ".synced_at"` — stores the float mtime of the NAS file at the time of last sync.

GET `/api/sync/status` returns `_status` dict — used by the frontend to show/hide the NAS banner.

In tests, monkeypatch `app.sync.NAS_SHARE_PATH` and `app.sync.LOCAL_DB_PATH` with `tmp_path` values.
The `reset_status` fixture in `tests/test_sync.py` is `autouse=True` and resets `_status` between tests.

## What is NOT implemented yet

- Background sync on interval (`sync_interval_minutes` from config)
- Sync on shutdown (`sync_on_shutdown` from config)
- CSV import
- Date-range filtering on the transactions endpoint
