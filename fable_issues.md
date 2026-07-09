# Faresight — Code Review Issues

A prioritized checklist of bugs, code smells, and improvement opportunities found in a full-codebase review.
Check items off as they are resolved. Each item's **Prompt** is a self-contained task brief that can be handed
to an Opus agent independently.

**Repo-wide constraints every prompt inherits** (from `CLAUDE.md`):

- Python 3.14 + Pydantic: use `Optional[T]` from `typing`, never `T | None`, in Pydantic models and FastAPI route signatures.
- Every change must include tests in `tests/` (use the `client` fixture and `make_tx` helper from `tests/conftest.py`; never touch the real `local_db_path`). Run the full suite `.venv/bin/pytest tests/ -v` before declaring done.
- Never start the server against the live DB — always `FARESIGHT_DB=$(mktemp --suffix=.db) uvicorn app.faresight:app --port 18765`.
- Importer sign invariant: debit columns → negative amounts, credit columns → positive amounts.
- `prompt-list.txt` is off-limits: never read, reference, or process it.
- After any change to `app/models.py`, update the `## Database schema` Mermaid block in `README.md`.

---

## Critical — data loss / data corruption

- [x] ### 1. Classification rules are wiped on every app startup

  **Files:** `app/database.py:174` (inside `migrate_db()`)

  **Problem:** `migrate_db()` runs on every boot and unconditionally executes
  `DROP TABLE IF EXISTS transaction_classification_rules` followed by a recreate. Every
  classification rule the user has created is silently deleted every time the app restarts.
  This was presumably a one-time migration to add a UNIQUE constraint, but it was written
  as an unconditional statement.

  **Prompt:**
  > In `app/database.py`, `migrate_db()` currently runs `DROP TABLE IF EXISTS transaction_classification_rules`
  > + recreate unconditionally on every startup (around line 174), destroying all user-created rules on every
  > restart. Make this migration conditional and data-preserving:
  > 1. Inspect the live schema first (`PRAGMA index_list('transaction_classification_rules')` /
  >    `sqlite_master`) and only rebuild the table if it exists **without** the expected UNIQUE constraint.
  > 2. When a rebuild is needed, preserve existing rows: rename the old table, create the new one via
  >    SQLAlchemy metadata or explicit DDL, copy rows across (`INSERT INTO ... SELECT ...`), then drop the
  >    renamed table.
  > 3. If the table already has the correct schema, `migrate_db()` must be a no-op for it.
  >
  > Add tests in `tests/` that: (a) create a rule, call `migrate_db()` again, and assert the rule survives;
  > (b) simulate the legacy schema (create the table without the UNIQUE constraint, insert a row) and assert
  > migration upgrades the schema while keeping the row. Follow the existing test patterns in
  > `tests/conftest.py` (in-memory SQLite via the `client` fixture; never the real DB). Run the full suite
  > `.venv/bin/pytest tests/ -v` before declaring done.

- [ ] ### 2. NAS push ignores a foreign lock — silently clobbers the other machine's data

  **Files:** `app/sync.py:281` (`sync_to_nas()`), periodic loop + shutdown push in `app/faresight.py`

  **Problem:** At startup, `sync_from_nas()` correctly detects a fresh foreign lock, sets
  `_status["lock_warning"] = <hostname>`, and skips the pull, waiting for the user to confirm via
  `POST /api/sync` ("Proceed anyway"). But `sync_to_nas()` checks only `sync_enabled` and NAS
  reachability — so the background periodic loop pushes the stale local DB **over** the other
  machine's NAS copy after `SYNC_INTERVAL_MINUTES`, and the graceful-shutdown push does the same,
  without the user ever clicking anything. The push even clears `lock_warning`, hiding the evidence.
  The same hazard exists after a `pull_failed_integrity` state.

  **Prompt:**
  > In `app/sync.py`, `sync_to_nas()` (around line 281) must not automatically push when another machine
  > holds the NAS. Read the "NAS sync — full lifecycle" section of `CLAUDE.md` first. Implement:
  > 1. Add a guard in `sync_to_nas()`: if `_status["lock_warning"]` is set (or a fresh foreign lock file
  >    exists on the NAS — re-check it, since the other machine may have locked *after* our startup),
  >    skip the push and record a status detail like `push_blocked_foreign_lock`. Apply the same guard
  >    when the last pull failed integrity checks (`pull_failed_integrity`).
  > 2. The explicit user override — `POST /api/sync` from the "Proceed anyway" button
  >    (`app/routers/sync.py`) — must still be able to push; give it an explicit `force=True` path that
  >    clears `lock_warning` deliberately, rather than the push clearing it as a side effect.
  > 3. The periodic asyncio loop and the shutdown push in `app/faresight.py` must use the guarded
  >    (non-force) path.
  >
  > Tests: in `tests/test_sync.py`, monkeypatch `app.sync.NAS_SHARE_PATH`, `app.sync.LOCAL_DB_PATH`,
  > `app.sync._OWN_HOSTNAME`, `app.sync.SYNC_INTERVAL_MINUTES` (the `autouse` `reset_status` fixture
  > resets `_status` between tests). Cover: foreign fresh lock → automatic push refused, NAS file
  > untouched; forced push via the API path → succeeds and claims the lock; stale foreign lock →
  > push allowed. Run the full suite before declaring done.

- [x] ### 3. Balance snapshots have no `as_of` arbitration — older statements regress the balance

  **Files:** `app/routers/transactions.py:471-473` (`import_bulk`), `app/models.py:160` (`BalanceSnapshot`)

  **Problem:** `import_bulk` applies `result.snapshot.amount` to `account.current_balance`
  unconditionally, but the `BalanceSnapshot` contract says the *newest* snapshot wins. If a user
  uploads an older statement after a newer one (out-of-order backfill), the account balance is
  overwritten with the stale older value.

  **Prompt:**
  > In `app/routers/transactions.py`, `import_bulk` (around lines 471–473) sets
  > `account.current_balance = result.snapshot.amount` whenever an importer returns a
  > `BalanceSnapshot`, regardless of the snapshot's `as_of` date. Per the contract documented on
  > `BalanceSnapshot` in `app/models.py`, snapshots are authoritative **set-to-latest**: only the
  > newest one should win. Fix:
  > 1. Before applying a snapshot, compare `snapshot.as_of` against the account's most recent
  >    balance-snapshot date — query the latest `as_of` from the `balance_history` table for that
  >    account (see `_log_balance` in the same file), or add a tracked column if cleaner.
  > 2. Apply the snapshot to `current_balance` only if it is newer (or equal — decide and document
  >    tie behavior); always still record it in `balance_history` for audit either way, unless that
  >    contradicts how `_log_balance` is used elsewhere — inspect and keep history semantics coherent.
  > 3. Keep the accumulate-inserted-rows path for snapshot-less files unchanged.
  >
  > Tests: upload (via the import API with a small CSV fixture, following existing importer tests in
  > `tests/`) a newer-dated statement then an older-dated one, and assert the balance stays at the
  > newer value; also assert the normal newer-after-older path still updates. Use in-memory DB
  > fixtures only. Run the full suite before declaring done.

## High — correctness

- [ ] ### 4. Dashboard charts mix income into "spending"

  **Files:** `frontend/assets/scripts/app.js:120` (and the by-category fetch nearby), `app/routers/transactions.py` summary endpoints, `app/buckets.py`

  **Problem:** The "Monthly Spending" bar chart fetches `/api/summary/by-month` with no
  `bucket=spend` filter, so it plots `Math.abs(...)` of the **net** of income + spending — months
  with big paychecks look like low-spend months. The "Spending by Category" pie
  (`/api/summary/by-category-for-period`) likewise includes income categories as slices.

  **Prompt:**
  > The dashboard (`frontend/assets/scripts/app.js`, fetches around line 120, and
  > `frontend/app/pages/index.html`) shows two charts labeled as *spending* but built from unfiltered
  > summary data. The backend already has DB-driven bucket support (`app/buckets.py`; the dashboard
  > badges use it). Fix:
  > 1. Pass `bucket=spend` when fetching `/api/summary/by-month` for the Monthly Spending bar chart —
  >    verify the endpoint in `app/routers/transactions.py` supports a `bucket` query param; if not,
  >    add it using the same bucket-filter helper the other summary endpoints use.
  > 2. Add/apply the same bucket filtering to `/api/summary/by-category-for-period` so the pie only
  >    shows spend-bucket categories.
  > 3. Keep param validation consistent (unknown bucket → 422, not silent fallback).
  >
  > Tests: extend the summary endpoint tests in `tests/` — seed both income and spend transactions
  > with `make_tx`, assert `bucket=spend` excludes income from by-month totals and from
  > by-category-for-period rows. UI: if there is an existing pattern for JS/UI tests, follow it;
  > otherwise assert the endpoints' filtered contract thoroughly. Use `Optional[T]` (not `T | None`)
  > for any new FastAPI query params. Run the full suite before declaring done.

- [ ] ### 5. Dual category fields (`category` vs `model_category`) with inconsistent semantics

  **Files:** `app/models.py`, `frontend/assets/scripts/common.js` (`saveCategory`), `app/importers/*.py`, `/api/summary/by-category` in `app/routers/transactions.py`

  **Problem:** Docs say `category` is the human-facing field and `model_category` /
  `model_confidence` are the model's suggestion that "never overwrites" it. In reality: the UI reads
  and edits only `model_category`; importers seed `category` with raw bank labels (Capital One) or
  `"Uncategorized"` (BofA); and `/api/summary/by-category` groups by the stale `category` field — an
  endpoint no page uses. The two fields have drifted into incoherence.

  **Prompt:**
  > The `Transaction` model (`app/models.py`) has both `category` and `model_category`, with drifted
  > semantics: the UI (`saveCategory` in `frontend/assets/scripts/common.js`, plus the table/edit
  > modal in `app.js`) only uses `model_category`; importers stuff raw bank labels or
  > `"Uncategorized"` into `category`; `/api/summary/by-category` groups by `category` and is unused
  > by any page. Survey all reads/writes of both fields (backend, importers, categorizer worker in
  > `app/categorizer.py`, all frontend scripts, tests), then unify:
  > 1. Make `model_category` + `model_confidence` the model-suggestion pair and keep exactly one
  >    canonical display field, honoring `user_modified_category` so human edits are never
  >    overwritten by the background categorizer. Preserve the documented rule: the worker writes
  >    only the model fields.
  > 2. Migrate existing data in `migrate_db()` (`app/database.py`) — raw SQL against the live SQLite
  >    file, consistent with the existing migration style. If `category` is dropped or repurposed,
  >    keep the raw bank label somewhere if it feeds classification rules; check `app/routers/rules.py`.
  > 3. Fix or delete `/api/summary/by-category` accordingly.
  > 4. Update `CLAUDE.md`'s categorization section and the README Mermaid schema to match.
  >
  > This is a design-decision task: write a short rationale at the top of your PR/commit description.
  > Tests: cover the migration, the categorizer non-overwrite guarantee, importer seeding, and the
  > summary endpoint. Run the full suite before declaring done.

- [ ] ### 6. Categorizer subprocess is unsupervised — a crash permanently stops categorization

  **Files:** `app/faresight.py:21-22` (`_spawn_categorizer`), shutdown handling around `app/faresight.py:43`, `app/categorizer.py` main loop

  **Problem:** The lifespan spawns `python -m app.categorizer` once. If the worker crashes, nothing
  restarts it — new transactions sit at `model_confidence = -1` forever and the UI pill just says
  "Stopped". Additional paper cuts: SIGINT is unhandled (ugly traceback in dev), the loop does an
  initial `time.sleep(interval)` before the first cycle, and SIGTERM arriving during the sleep can
  blow past the 10s `wait()` timeout, forcing an unnecessary `kill()`.

  **Prompt:**
  > `app/faresight.py` spawns the categorizer worker subprocess (`_spawn_categorizer`, line ~21) once
  > at startup with no supervision. Improve robustness:
  > 1. Supervise the worker: a small asyncio task in the lifespan that periodically `poll()`s the
  >    Popen handle and respawns on unexpected exit (with basic backoff so a crash-looping worker
  >    doesn't spin). Must not respawn during shutdown.
  > 2. In `app/categorizer.py`: run the first categorization cycle immediately on start instead of
  >    sleeping first; handle SIGINT/SIGTERM cleanly (catch `KeyboardInterrupt`, or install a signal
  >    handler that sets a stop flag); make the inter-cycle wait interruptible (e.g. sleep in short
  >    increments or use an `Event.wait(timeout)`) so shutdown completes within the existing 10s
  >    `wait()` in `faresight.py` without escalating to `kill()`.
  > 3. Keep the existing behavior that Ollama being down just leaves rows at `-1` for retry.
  >
  > Tests: the HTTP/process touch-points (`_get_tags`, `_start_ollama`, `_generate`) are already
  > isolated for monkeypatching — follow the existing categorizer test patterns. Test the supervisor
  > respawn logic with a fake Popen object (poll() returning an exit code), and the stop-flag path.
  > Never start a real server against the live DB. Run the full suite before declaring done.

## Medium — robustness / hygiene

- [ ] ### 7. Unvalidated query params on list endpoints

  **Files:** `app/routers/transactions.py` (`GET /api/transactions`, `_filter_by_account_type` at line 27, insights limit)

  **Problem:** `page=0` or negative produces a negative SQL OFFSET; `limit=0` causes a client-side
  division by zero in Tabulator's `last_page` math; an unknown `account_type` string silently falls
  through to the "bank" behavior in `_filter_by_account_type` instead of erroring.

  **Prompt:**
  > In `app/routers/transactions.py`, harden the list endpoints' query params:
  > 1. `GET /api/transactions`: constrain `page` with `Query(ge=1)` and `limit` with
  >    `Query(ge=1, le=<sensible max, e.g. 500>)` so bad values return 422 instead of producing a
  >    negative OFFSET or a client-side division by zero.
  > 2. `_filter_by_account_type` (line ~27): validate `account_type` against an explicit whitelist
  >    derived from the `AccountType` enum in `app/models.py`; unknown values → 422, not silent
  >    "bank" behavior. Prefer typing the param as `Optional[<enum or Literal>]` — remember
  >    Python 3.14 requires `Optional[T]`, never `T | None`, in route signatures.
  > 3. Apply the same `limit` constraint to the insights endpoint (`app/routers/insights.py` or
  >    wherever it lives — locate it first).
  >
  > Tests: parametrized cases for `page=0`, `page=-1`, `limit=0`, over-max limit, bogus
  > `account_type` → all 422; valid values unchanged. Check `frontend/assets/scripts/app.js` doesn't
  > ever send the now-invalid values. Run the full suite before declaring done.

- [ ] ### 8. Category deletion has no referential guards for rules

  **Files:** `app/routers/categories.py:53` (`delete_category`), `app/routers/rules.py`

  **Problem:** `DELETE /api/categories/{name}` deletes the category row but ignores classification
  `Rule` rows that assign that category — those rules keep firing and stamping a nonexistent label
  onto new transactions. (Orphaned labels on existing transactions are accepted by design and render
  magenta, so transactions don't need cascading — but rules do.)

  **Prompt:**
  > In `app/routers/categories.py`, `delete_category` (line ~53) ignores rules referencing the
  > category. Look at `app/routers/rules.py` and the rule model to see how rules store their target
  > category. Then implement one of (pick and justify): (a) block deletion with a 409 listing the
  > dependent rules, or (b) cascade-delete the dependent rules in the same transaction. Given the
  > frontend has rules-management UI on the upload page (`frontend/assets/scripts/upload.js`), a 409
  > with a clear error message the UI can display is likely the safer default — but inspect the UI
  > flow first and wire the error display (the app uses Bootstrap banners/modals, not `alert()`,
  > for new UI). Orphaned labels on existing *transactions* are intentional (they render magenta) —
  > do not cascade those.
  >
  > Tests: deleting a category with no rules → 204 as today; with dependent rules → chosen behavior
  > (409 with rule names, or rules gone). Run the full suite before declaring done.

- [ ] ### 9. NAS pushed every interval even when nothing changed

  **Files:** `app/sync.py` (`sync_to_nas`), periodic loop in `app/faresight.py`

  **Problem:** The periodic loop copies the entire SQLite DB to the NAS every
  `SYNC_INTERVAL_MINUTES` regardless of whether anything was written locally — needless NAS I/O and
  churn of the lock/marker files.

  **Prompt:**
  > Make periodic NAS pushes skip when the local DB hasn't changed since the last successful push.
  > In `app/sync.py`, track change state via SQLite `PRAGMA data_version` (cheap, increments on any
  > commit by another connection) or the local DB file's mtime captured at last push — pick whichever
  > is more reliable given WAL mode is enabled (`app/database.py`); note `data_version` is
  > per-connection and only reflects *other* connections' commits, so mtime (checking the `-wal` file
  > too) may be simpler. Record the marker after each successful push; in `sync_to_nas()` skip with
  > `last_action = "skipped_unchanged"` (add to the documented `_status` keys) when unchanged.
  > The explicit `POST /api/sync` "Sync now" path should still force a push. Coordinate with issue #2's
  > force/guard split if already implemented.
  >
  > Tests in `tests/test_sync.py` (monkeypatch the module paths per `CLAUDE.md`): unchanged DB →
  > push skipped, NAS mtime untouched; after a local write → push happens; forced push → always
  > happens. Update `CLAUDE.md`'s `_status` keys list. Run the full suite before declaring done.

- [ ] ### 10. PATCH endpoints skip validation that POST enforces

  **Files:** `app/routers/transactions.py` (`update_transaction`), `app/schemas.py`

  **Problem:** `update_transaction` accepts a nonexistent `account_id` (create validates it exists),
  silently producing an orphaned FK. Also, nothing server-side sets `user_modified_category` when a
  client PATCHes `category`/`model_category` — the UI must remember to send it, and any other client
  (curl, future integrations) will silently create rows the background categorizer may overwrite.

  **Prompt:**
  > In `app/routers/transactions.py`, `update_transaction`:
  > 1. If the PATCH payload includes `account_id`, validate the account exists (mirror the POST-path
  >    validation) → 404/422 on unknown id.
  > 2. If the payload changes the category field(s) and does not explicitly set
  >    `user_modified_category`, default it to `True` server-side, so the background categorizer
  >    (`app/categorizer.py`) never overwrites a human edit made through the raw API. Check how
  >    `saveCategory` in `frontend/assets/scripts/common.js` sends its PATCH today and keep it
  >    working (it may become redundant — fine).
  > 3. Audit the other PATCH endpoints (accounts, rules, categories) for the same
  >    create-validates/update-doesn't asymmetry and fix any you find the same way.
  >
  > Schemas live in `app/schemas.py` — remember `Optional[T]`, never `T | None`. Tests: PATCH with
  > bogus `account_id` → error; PATCH changing category without the flag → flag set and categorizer
  > skips the row; explicit `user_modified_category=false` in payload → respected. Run the full
  > suite before declaring done.

- [ ] ### 11. Documentation drift — CLAUDE.md and README ER diagram

  **Files:** `CLAUDE.md`, `README.md`

  **Problem:** `CLAUDE.md` describes the categorizer as an in-app asyncio task
  (`_categorization_loop`) and `build_prompt()` as "a stub" — both stale (it's a subprocess with a
  real prompt now). The router list omits `categories` and `insights`; the served-pages list omits
  `/income` and `/expenses`; "What is NOT implemented yet" may be stale. The README Mermaid schema
  may be missing `balance_history`, `categories`, `file_imports`, and `Transaction.reference_number`.

  **Prompt:**
  > Bring the docs back in sync with the code. Read `app/faresight.py`, `app/categorizer.py`, all of
  > `app/routers/`, `app/models.py`, and the `frontend/app/pages/` directory, then:
  > 1. Fix `CLAUDE.md`: the categorization section (subprocess worker spawned via
  >    `python -m app.categorizer`, supervised per issue #6 if landed; real prompt, not a stub), the
  >    router list (add `categories`, `insights`, and any others present), the served-pages list
  >    (`/income`, `/expenses`, ...), and prune "What is NOT implemented yet" of anything now done.
  > 2. Verify every remaining claim in `CLAUDE.md` against the code while you're in there — fix any
  >    other drift you find (file paths, `_status` keys, importer notes).
  > 3. Regenerate/verify the `## Database schema` Mermaid block in `README.md` against
  >    `app/models.py` — it must include every table (`balance_history`, `categories`,
  >    `file_imports`, etc.) and current columns like `Transaction.reference_number`.
  >
  > Do NOT read or mention `prompt-list.txt`. No code changes, so no new tests — but run
  > `.venv/bin/pytest tests/ -v` once to confirm nothing was accidentally touched.

- [ ] ### 12. Dead code / repo hygiene

  **Files:** `app/nas.py`, `app/config.py:19-20`, `frontend/assets/scripts/accounts.js`, git index

  **Problem:** Several leftovers: `app/nas.py` is a `NotImplementedError` placeholder superseded by
  `app/sync.py`; `SYNC_ON_STARTUP`/`SYNC_ON_SHUTDOWN` are loaded in `app/config.py:19-20` but never
  read anywhere (misleading — sync always runs regardless of config); the `_editingAccountId` edit
  branch in `accounts.js` is unreachable (never set non-null); and the git index has stale
  added-then-deleted entries for `app/accounts.py` and `app/importers/sample.py` (status `AD`).

  **Prompt:**
  > Repo hygiene pass:
  > 1. Delete `app/nas.py` after grepping the whole repo (code, tests, docs) for imports/references —
  >    it's a NotImplementedError placeholder superseded by `app/sync.py`.
  > 2. In `app/config.py` (lines ~19–20), `SYNC_ON_STARTUP` / `SYNC_ON_SHUTDOWN` are loaded but never
  >    used. Grep to confirm, then either remove them (and any mention in `config.yaml` /
  >    `config.yaml.example` / docs) or actually honor them in the lifespan — removing is the simpler
  >    correct choice unless the user has them set in their real `config.yaml`; prefer removal and
  >    note it.
  > 3. In `frontend/assets/scripts/accounts.js`, `_editingAccountId` is never assigned a non-null
  >    value, making the edit branch dead. Decide: remove the dead branch, or wire up account editing
  >    if the modal/UI clearly intended it — inspect `frontend/app/pages/accounts.html` to judge
  >    intent; if editing was intended, implementing it is in scope (with endpoint checks against
  >    `app/routers/accounts.py`).
  > 4. Clean the stale git index entries for `app/accounts.py` and `app/importers/sample.py`
  >    (`git rm --cached` / staging the deletion) so `git status` is clean of the `AD` pairs. Do not
  >    commit unless asked — just stage correctly.
  >
  > Tests: whatever remains must pass; add tests only if you wire up account editing. Run the full
  > suite before declaring done.

## Low — polish

- [ ] ### 13. Frontend paper cuts

  **Files:** `frontend/assets/scripts/common.js`, `frontend/assets/scripts/upload.js`, `app/faresight.py:62`

  **Problem:** (a) Creating a rule via the shared modal doesn't refresh the upload page's rules
  table — `saveRule` has no post-save hook, unlike `afterCategorySave`. (b) `applyRule`,
  `deleteRule`, and the account modals use native `alert()`/`confirm()` while the rest of the app
  uses Bootstrap modals/banners. (c) `esc()` doesn't escape single quotes — fragile if anyone writes
  single-quoted HTML attributes. (d) `amountFormatter`/`openEditModal` have no NaN guards.
  (e) Jinja is configured with `cache_size=0`, disabling template caching in production.

  **Prompt:**
  > Frontend polish pass across `frontend/assets/scripts/common.js`, `app.js`, `upload.js`,
  > `accounts.js`:
  > 1. Add a post-save hook to `saveRule` in `common.js` mirroring the existing `afterCategorySave`
  >    pattern, and have `upload.js` register a callback that refreshes its rules table.
  > 2. Replace native `alert()`/`confirm()` in `applyRule`, `deleteRule`, and the account modals with
  >    the app's Bootstrap modal/banner patterns (see how other confirmations are done in these
  >    files; extract a shared confirm-modal helper into `common.js` rather than copy-pasting — the
  >    project prefers shared helpers over duplication).
  > 3. Make `esc()` also escape single quotes (`&#39;`).
  > 4. Add NaN guards to `amountFormatter` and `openEditModal` (render a safe fallback like `—`).
  > 5. In `app/faresight.py` (line ~62), remove `cache_size=0` from the Jinja environment (or gate it
  >    on a debug flag) so templates are cached normally.
  >
  > Follow the Font Awesome rules in `CLAUDE.md` for any icons (free set only; prefer `fa-regular`,
  > never `fa-light`/`fa-thin`/`fa-duotone`). Test UI behavior to the extent the existing test setup
  > allows; at minimum the Python-side change is covered and the suite passes. Run the full suite
  > before declaring done.

- [ ] ### 14. Categorizer logging noise

  **Files:** `app/categorizer.py:218,277,280`

  **Problem:** f-strings are passed to `logger.info(...)`, defeating lazy `%`-formatting (the string
  is built even when the level is filtered), and per-item "Processing {...}" lines at INFO are noisy
  in normal operation.

  **Prompt:**
  > In `app/categorizer.py` (lines ~218, 277, 280 and anywhere else in the file), convert f-string
  > logging calls to lazy `%`-style (`logger.info("Processed %d rows", n)`), and demote per-item
  > "Processing ..." lines from INFO to DEBUG, keeping batch-level summaries at INFO. Sweep the rest
  > of `app/` for the same f-string-in-logger pattern and fix those too. No behavior change; run
  > `.venv/bin/pytest tests/ -v` to confirm nothing regressed (some tests may assert on log output —
  > update them if so).

- [ ] ### 15. Date-range filtering on `/api/transactions`

  **Files:** `app/routers/transactions.py`, `frontend/assets/scripts/app.js`, `CLAUDE.md`

  **Problem:** The one feature explicitly documented as missing in `CLAUDE.md` ("What is NOT
  implemented yet"). The transactions list cannot be filtered by date range.

  **Prompt:**
  > Implement date-range filtering on `GET /api/transactions` in `app/routers/transactions.py`:
  > 1. Add `date_from` / `date_to` query params (`Optional[date]` — remember Python 3.14 requires
  >    `Optional[T]`, never `date | None`, in route signatures), inclusive on both ends, applied
  >    before pagination so `total`/`last_page` reflect the filter. Validate `date_from <= date_to`
  >    (422 otherwise).
  > 2. Consider whether the summary endpoints should share the same helper — extract a shared
  >    filter function if it reduces duplication (project preference: shared helpers over
  >    copy-paste).
  > 3. Wire the frontend where transactions are browsed — check which pages own transaction tables
  >    now (Income/Expenses pages per recent refactors, plus `app.js`) and add date pickers feeding
  >    the params into the Tabulator ajax config.
  > 4. Remove the item from `CLAUDE.md`'s "What is NOT implemented yet" section.
  >
  > Tests: seed transactions across several dates with `make_tx`; assert from-only, to-only, both,
  > inverted range → 422, and pagination totals under filter. Run the full suite before declaring
  > done.
