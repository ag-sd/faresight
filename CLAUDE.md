# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Off-limits files

- **`prompt-list.txt`** — user's private notes. Never read, process, reference, or act on this file under any circumstances.

## Project summary

Local expense tracker. FastAPI backend + SQLite (SQLAlchemy 2) + Bootstrap 5.3
HTML/JS frontend with Chart.js.

## Commands

```bash
# Run the app
source .venv/bin/activate
uvicorn app.faresight:app --reload

# Run all tests
.venv/bin/pytest tests/ -v

# Run a single test file
.venv/bin/pytest tests/test_transactions.py -v

# Run a single test by name
.venv/bin/pytest tests/test_transactions.py::test_create_transaction -v
```

## Architecture

Routes are split into routers under `app/routers/`:
- `app/routers/transactions.py` — CRUD + summary/chart endpoints + CSV import (`/api/transactions`, `/api/summary/*`, `/api/categories`)
- `app/routers/accounts.py` — account management (`/api/accounts`, `/api/accounts/bank-logos`)
- `app/routers/sync.py` — NAS sync control (`/api/sync`, `/api/sync/status`, `/api/sync/go-offline`)

`app/faresight.py` wires the routers, mounts `/static → frontend/`, handles the lifespan (DB creation → `migrate_db()` → `sync_from_nas()` → periodic sync loop → shutdown push), and serves the two HTML pages at `/` and `/accounts`.

**Schema migrations** are handled by `migrate_db()` in `app/database.py` — raw `ALTER TABLE` / `RENAME COLUMN` SQL against the live SQLite file. Add new migrations there when adding columns to existing tables.

## Key files

- `app/config.py` — loads `config.yaml`; exports `NAS_SHARE_PATH`, `LOCAL_DB_PATH`, `SYNC_INTERVAL_MINUTES`, `BANK_LOGOS`
- `app/database.py` — SQLAlchemy engine + `migrate_db()` for schema evolution
- `app/models.py` — `Transaction` and `Account` tables; `AccountType` and `SourceFrequency` enums
- `app/schemas.py` — Pydantic schemas for all request/response types
- `app/sync.py` — full NAS sync state machine; see NAS sync section below
- `frontend/assets/scripts/app.js` — all frontend JavaScript
- `frontend/app/pages/index.html` — main dashboard (transactions + charts)
- `frontend/app/pages/accounts.html` — accounts management page

## Frontend libraries

- **Font Awesome 6 free** (6.7.2) via jsDelivr CDN — included in all pages.
  Always use the `fa-regular` prefix (e.g. `fa-regular fa-pen-to-square`). Never use `fa-solid` or `fa-light`.

## Development rules

- **Always add tests.** Every code change — new feature, bug fix, refactor — must include
  corresponding tests in `tests/`. Run `pytest` before declaring work done. List coverage once done.
- Tests use an in-memory SQLite DB via the `client` fixture in `tests/conftest.py`.
  Never write tests that touch the real `local_db_path`.
- `conftest.py` also provides `make_tx(client, **kwargs)` — a helper to POST a transaction with
  sensible defaults. Use it instead of repeating the payload boilerplate.
- Try to write tests for the UI as well to the best extent possible.

## Runtime notes

- Python 3.14 is in use. Use `Optional[T]` from `typing` instead of `T | None`
  in Pydantic models and FastAPI route signatures — the `X | None` union syntax
  triggers a Pydantic evaluation bug on Python 3.14.
- Virtualenv lives at `.venv/`; activate with `source .venv/bin/activate`
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

**Frontend banners:**
- Lock conflict → red banner with [Proceed anyway] / [Work offline]
- NAS unreachable → yellow banner
- Pull / push success → green banner
- "Sync now" button always visible in the header

In tests, monkeypatch `app.sync.NAS_SHARE_PATH`, `app.sync.LOCAL_DB_PATH`, `app.sync._OWN_HOSTNAME`, and `app.sync.SYNC_INTERVAL_MINUTES`.
The `autouse=True` `reset_status` fixture in `tests/test_sync.py` resets all six `_status` fields between tests.

## What is NOT implemented yet

- Date-range filtering on the transactions endpoint
