# Faresight — Claude Context

## Off-limits files

- **`prompt-list.txt`** — user's private notes. Never read, process, reference, or act on this file under any circumstances.

## Project summary

Local expense tracker. FastAPI backend + SQLite (SQLAlchemy 2) + Bootstrap 5.3
HTML/JS frontend with Chart.js.

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
- `app/faresight.py` — all routes; serves `frontend/app/pages/index.html` at `/`
- `app/nas.py` — stub only; raises `NotImplementedError`
- `frontend/assets/css/app.css` — custom CSS (chart container height, tabular-nums)
- `frontend/assets/scripts/app.js` — all frontend JavaScript (API helpers, charts, forms, NAS banners)
- `frontend/app/pages/index.html` — dashboard HTML; Bootstrap 5.3 + Chart.js 4 via CDN; references `/static/assets/`

## Development rules

- **Always add tests.** Every code change — new feature, bug fix, refactor — must include
  corresponding tests in `tests/`. Run `pytest` before declaring work done. List coverage once done
- Tests use an in-memory SQLite DB via the `client` fixture in `tests/conftest.py`.
  Never write tests that touch the real `local_db_path`.
- Try to write tests for the UI as well to the best extent possible.

## Runtime notes

- Python 3.14 is in use. Use `Optional[T]` from `typing` instead of `T | None`
  in Pydantic models and FastAPI route signatures — the `X | None` union syntax
  triggers a Pydantic evaluation bug on Python 3.14.
- Virtualenv lives at `.venv/`; activate with `source .venv/bin/activate`
- Run: `uvicorn app.faresight:app --reload`
- DB is created automatically at `~/.local/share/expense-tracker/local.db`

## NAS sync (`app/sync.py`) — full lifecycle

`sync_from_nas()` runs once at startup (inside the FastAPI lifespan, before requests).
It is synchronous — no threads, no scheduler.

**Startup — `sync_from_nas()`** (called in lifespan before requests):
1. NAS dir unreachable → warn, continue offline; `reachable = False`
2. Foreign active lock → set `lock_warning = <hostname>`, skip pull; user confirms via POST /api/sync
3. NAS file absent → push local DB up (first run); `last_action = "pushed_initial"`
4. NAS mtime > marker → backup local to `.db.bak`, pull NAS down; `last_action = "pulled_update"`
5. Local current → skip; `last_action = "skipped_current"`
6. After any successful sync → write `.lock` file claiming ownership

**Push — `sync_to_nas()`** — called by:
- Background asyncio loop every `sync_interval_minutes`
- Graceful shutdown (after loop is cancelled)
- `POST /api/sync` (Sync Now / Proceed Anyway buttons)

**Lock file** — `<nas_share_path>.lock` (JSON: `{hostname, timestamp}`):
- Written after every successful sync to claim ownership
- Fresh = age < `sync_interval_minutes * 60` seconds
- Stale locks are silently ignored
- Released on shutdown (`_release_lock()`) — only if hostname matches ours

**`_status` keys:** `reachable`, `last_action`, `detail`, `lock_warning`, `last_push`, `sync_enabled`

**API:**
- `GET /api/sync/status` — returns `_status`
- `POST /api/sync` — push now (also used for "Proceed anyway")
- `POST /api/sync/go-offline` — disables NAS sync for this session

**Frontend banners:**
- Lock conflict → red banner with [Proceed anyway] / [Work offline]
- NAS unreachable → yellow banner
- Pull / push success → green banner
- "Sync now" button always visible in the header

In tests, monkeypatch `app.sync.NAS_SHARE_PATH`, `app.sync.LOCAL_DB_PATH`, `app.sync._OWN_HOSTNAME`, and `app.sync.SYNC_INTERVAL_MINUTES`.
The `autouse=True` `reset_status` fixture in `tests/test_sync.py` resets all six `_status` fields between tests.

## What is NOT implemented yet

- Date-range filtering on the transactions endpoint
