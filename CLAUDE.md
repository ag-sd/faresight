# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.


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
- `app/routers/rules.py` — classification rules CRUD + retroactive apply (`/api/rules`); rule descriptions are regex patterns (case-insensitive `re.search`, shared matching in `app/rule_matching.py`)
- `app/routers/sync.py` — NAS sync control (`/api/sync`, `/api/sync/status`, `/api/sync/go-offline`)

`app/faresight.py` wires the routers, mounts `/static → frontend/`, handles the lifespan (DB creation → `migrate_db()` → `sync_from_nas()` → periodic sync loop + categorizer subprocess with supervisor → shutdown push), and serves the three HTML pages at `/`, `/accounts`, and `/upload`.

**Schema migrations** are handled by `migrate_db()` in `app/database.py` — raw `ALTER TABLE` / `RENAME COLUMN` SQL against the live SQLite file. Add new migrations there when adding columns to existing tables.

## Key files

- `app/config.py` — loads `config.yaml`; exports `NAS_SHARE_PATH`, `LOCAL_DB_PATH`, `SYNC_INTERVAL_MINUTES`, `BANK_LOGOS`
- `app/database.py` — SQLAlchemy engine + `migrate_db()` for schema evolution
- `app/models.py` — `Transaction` and `Account` tables; `AccountType` and `SourceFrequency` enums
- `app/schemas.py` — Pydantic schemas for all request/response types
- `app/sync.py` — full NAS sync state machine; see NAS sync section below
- `frontend/assets/scripts/common.js` — shared JS (API helper, NAS banners, category colours, rules modals)
- `frontend/assets/scripts/app.js` — dashboard-specific JS (transactions table, charts)
- `frontend/assets/scripts/upload.js` — upload-page JS (dropzone, importer, rules table)
- `frontend/app/pages/index.html` — main dashboard (transactions + charts)
- `frontend/app/pages/accounts.html` — accounts management page
- `frontend/app/pages/upload.html` — CSV upload + classification rules page

## Frontend libraries

All loaded via jsDelivr CDN — no local copies.

| Library | Version | Notes |
|---|---|---|
| Bootstrap | 5.3.3 | CSS framework + JS bundle (modals, collapse, etc.) |
| Font Awesome Free | 6.7.2 | Icons — see rules below |
| Chart.js | 4 | Charts on the dashboard |
| Tabulator | 6.3.0 | Sortable/paginated data tables |

**Font Awesome Free icon rules:**  
FA 6 Free ships three styles: `fa-solid` (most icons), `fa-regular` (a small subset), and `fa-brands`.  
Prefer `fa-regular` where the icon exists in that weight. Use `fa-solid` when it does not (e.g. `fa-play` is solid-only in the free set).  
Never use `fa-light`, `fa-thin`, or `fa-duotone` — those are Pro-only and will render nothing.

## Development rules

- **Always add tests.** Every code change — new feature, bug fix, refactor — must include
  corresponding tests in `tests/`. Run `pytest` before declaring work done. List coverage once done.
- Tests use an in-memory SQLite DB via the `client` fixture in `tests/conftest.py`.
  Never write tests that touch the real `local_db_path`.
- `conftest.py` also provides `make_tx(client, **kwargs)` — a helper to POST a transaction with
  sensible defaults. Use it instead of repeating the payload boilerplate.
- Try to write tests for the UI as well to the best extent possible.

## Safe server launch (verification / manual testing)

**Never start the server without overriding the DB path.** The live DB at
`~/.local/share/expense-tracker/local.db` contains real user data.

Always use the `FARESIGHT_DB` env var to point the server at a temp file:

```bash
FARESIGHT_DB=$(mktemp --suffix=.db) uvicorn app.faresight:app --port 18765
```

The `.claude/skills/verifier-server.md` skill encodes the full safe-launch
recipe and is picked up automatically by `/verify`.

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

## Importer conventions (`app/importers/`)

- Each bank module lives in `app/importers/<bank>.py` and exports one or more import functions.
- Import functions are registered by name in `app/importers/__init__.py` — the module itself does **not** own its display name.
- **Debit columns = negative amounts; credit columns = positive amounts.** This is an invariant across all importers. A debit is a charge the account holder owes; a credit is a payment or refund reducing the balance. Use `CsvImporter.signed_amount(debit, credit)` (`app/importers/base.py`) rather than re-implementing it.
- Sample fixture CSVs for each importer live in `tests/` (e.g. `tests/capitalone_sample.csv`).

**`CsvImporter` base class (`app/importers/base.py`) — Template Method.**
Importers subclass `CsvImporter[C]` and implement one method:
- `parse_row(row, account, ctx) -> Optional[TransactionCreate]` — map one CSV row.
  Return `None` to **skip** a row (no error); raise `ValueError`/`KeyError` to record
  a per-row error (captured as `Row {n}: ...`, `n` is 1-based from the header).
- The base's `run()` owns the invariant skeleton (decode `utf-8-sig` → `DictReader` →
  row loop → error capture) and stamps identity (`account_id`, `filename`, `importer`)
  onto the result. It is reentrant: one instance can import many files.
- **Per-file state** goes in a fresh *context* from `new_context()` (concrete hook,
  default `{}`) — override to return a typed dataclass. Threading state through `ctx`
  (not `self`) keeps `parse_row` pure and unit-testable in isolation.
- Module-level wrapper functions (`import_*_csv`) instantiate the class and call
  `run()`; the registry maps display names to these wrappers.

**`ImportResult` balance fields (`app/models.py`)** — two *distinct* concepts:
- `net_delta` — sum of the file's transaction amounts (a *change*). Always computed by
  the base. NOT a balance until added to a prior balance.
- `snapshot: Optional[BalanceSnapshot]` — an authoritative balance (`amount`, `as_of`)
  the file literally states (e.g. a `Balance` column). Returned via the
  `balance_snapshot(ctx)` hook; most importers return `None`.

`import_bulk` applies snapshots to `account.current_balance` when present (authoritative,
set-to-latest). For snapshot-less files (credit cards) it accumulates the sum of the rows
**actually inserted** after dedupe — never `result.net_delta`, which ignores dedupe.

**Import idempotency (`import_bulk` + `_dedupe_rows` in `app/routers/transactions.py`)** —
two layers, both required because legitimate duplicate transactions exist (same
account/day/vendor/amount, e.g. two bus fares) so **no uniqueness constraint is possible**:
- **Layer 1 — exact file:** SHA-256 of the raw bytes in `FileImport.content_hash`; a
  re-upload of identical bytes to the same account (prior `rows_persisted > 0`) is
  short-circuited with `duplicate_file: true` and no new `FileImport` row.
- **Layer 2 — occurrence counting:** each row gets `Transaction.dedup_hash`
  (`dedup_hash_for()` in `app/models.py`, **non-unique** index). Per hash, only
  file-count − DB-count copies insert. The hash is stamped at insert (imports *and* manual
  `POST /api/transactions`) and never recomputed on edit. A previous attempt (`hash_code` +
  unique index) was reverted; `migrate_db()` still drops that column — do not reuse the name.
  - **Identity:** when an importer supplies a stable bank transaction ID via
    `TransactionCreate.reference_number` (e.g. BofA's Reference Number, persisted on
    `Transaction.reference_number`), `dedup_hash_for(..., reference=)` hashes
    `account_id|ref|<reference>` — this survives pending→posted description/amount rewrites.
    Otherwise it falls back to the content tuple `account_id|date|description|amount:.2f`.

## Transaction categorization (`app/categorizer.py`)

Categorization runs asynchronously via a background worker subprocess — it does
**not** block the upload response.

**Category field model** (unified 2026-07):
- `model_category` + `model_confidence` — the model-suggestion pair **and** the canonical
  display field. Everything user-facing (tables, edit modal, summaries, buckets, insights)
  reads and writes `model_category`.
- `bank_category` — the raw category label from the bank's CSV export (or `"Uncategorized"`).
  Used **only** as the `bank_category_hint` sent to the LLM; never displayed.
- `user_modified_category` — set `True` by user edits; the worker's write-back and rule
  retro-apply are both guarded on it, so human edits are never overwritten.
- Manual `POST /api/transactions` accepts an optional `category` (human choice): it is written
  to `model_category` with confidence 10 and `user_modified_category=True` — the row is
  pre-categorized and never queued. Omitted → the row queues for AI categorization as usual.

**`model_confidence` sentinel values:**
- `None` — legacy only; not present in normal flow after the `-1` default was introduced
- `-1` (`PENDING_CONFIDENCE`) — queued for categorization, not yet processed (default for all new rows)
- `0–10` — categorized (0 = fallback / low confidence, 10 = highest)

`TransactionOut` maps `-1 → null` before returning to clients so the API surface stays clean.

**Upload flow** (`import_bulk` in `app/routers/transactions.py`): parsed rows are saved
immediately with `model_confidence = -1`. No Ollama call on the hot path.

**Background worker** (`python -m app.categorizer`, spawned as a subprocess in the lifespan):
polls every `categorization_poll_interval_s` seconds (config default: 10). Each cycle opens its
own `SessionLocal()` session, queries for `-1` rows, categorizes them in batches
(`_categorize_pending`), and writes results back. If Ollama is down, the rows stay at `-1` and
are retried next cycle.

**Worker supervision** (`_supervise_categorizer` in `app/faresight.py`): an asyncio task polls
the Popen handle every 5s and respawns it on unexpected exit, with exponential backoff
(1s → 60s cap) that resets once a poll finds the child healthy. Respawns update
`app.state.cat_proc` so `/api/categorizer/running` stays truthful. On shutdown the supervisor is
cancelled *before* `terminate()`, so the intentional exit is never respawned. The worker's main
loop (`_main_loop`) runs its first cycle immediately and waits on a `threading.Event` between
cycles; SIGTERM/SIGINT set the event, so shutdown completes well inside the parent's 10s grace
period.

- Allowed categories are loaded from the DB each cycle (`_load_category_data`), so user edits
  take effect without a restart. Confidence is an int on a 0–10 scale.
- `BATCH_SIZE = 5`. A batch failure never aborts the run: parse/infer failures retry once, then
  fall back to `"Other"` / confidence 0.
- `ensure_ollama_running()` health-checks `{OLLAMA_HOST}/api/tags`, starts `ollama serve` if down
  (detached, never killed), polls up to 30s, and confirms the model is present.
- `build_prompt()` includes each row's `bank_category_hint` (from `bank_category`, empty when
  `"Uncategorized"`), explicitly marked advisory-only.
- HTTP/process touch-points are isolated as `_get_tags`, `_start_ollama`, `_generate` so tests
  monkeypatch them and never hit a real server. `OLLAMA_HOST` lives in `app/config.py` /
  `config.yaml` (default `http://localhost:11434`).
- SQLite WAL mode is enabled in `app/database.py` to prevent write contention between the
  background worker and request handlers.
