# Dashboard Analytics — Prerequisites TODO

## Context

We want to turn the dashboard into a finance **overview** (net-worth / spend / income /
savings-rate badges, a cash-flow chart, cleaner spending-by-category, net worth over time).
Exploration showed the current data model can't support these honestly yet.

**This document is only the prerequisite list — the data/model groundwork that must land
*before* any new charting or widget work begins.** Charting is explicitly out of scope here
and is tracked separately (see "Out of scope" at the bottom).

### Decisions that shape the prerequisites
- **Card balances → derive from transactions** (running sum of `net_delta`), not a manual
  field → makes the re-import dedupe guard mandatory.
- **Income → monthly history**, derived from `Income` + `Interest Income` transactions. No new
  income entity table; it's an aggregation.
- **Interest → derived** from `Interest Income` / `Interest Paid` categories (already exist).

### Why each is needed (findings from exploration)
- Bucketing half-done: `_exclude_transfers()` (`app/routers/transactions.py:40-50`) drops
  `Payments`/`Transfers`, but summaries `SUM(amount)` across everything else — **income and spend
  are mixed**, no separation exists.
- Net-worth hole: `current_balance` (`app/models.py:66`) is set only from `BalanceSnapshot` on
  import (`transactions.py:291`); **credit cards carry no snapshot**, so liabilities are missing.
- No historical balance storage (single overwritten scalar).
- No re-import idempotency — CLAUDE.md lists applying `net_delta` to `current_balance` as NOT
  implemented, gated on a dedupe guard. Transactions have no natural dedup key.
- No summary response schemas — endpoints return plain dicts.

---

## Prerequisites (in dependency order)

### [x] P1 — Category bucketing DB table + management UI  · *foundational, no deps*
Categories are stored in a `categories` SQLite table (name, color, bucket, description, sort_order)
seeded with 15 defaults from `app/category_defaults.py`. CRUD API at `/api/categories`. Frontend
"Categories" tab in the upload page's Classification card (Tabulator table, inline editing, create
modal with color picker). All hard-coded category constants removed:
- `CATEGORY_DESCRIPTIONS`, `ALLOWED_CATEGORIES`, `_CANONICAL`, `_CATEGORY_BLOCK` removed from
  `app/categorizer.py`; replaced with `_load_category_data(db)` called each poll cycle.
- `CATEGORY_COLORS` removed from `frontend/assets/scripts/common.js`; replaced with `loadCategories()`
  fetching `/api/categories`. Unknown/orphaned categories render magenta (`#ff2d78`).
- `_exclude_transfers()` replaced by `_exclude_internal(q, db)` querying `bucket='internal'`.
- Rule creation validates category against the DB table (no FK, string match).

### [x] P2 — Re-import dedupe / idempotency guard  · *gates all balance work*
Card balances derive from a running sum of `net_delta`; without dedupe, re-importing a file
double-counts. Design a per-transaction identity (e.g. content hash of
`account_id + date + description + amount`, or a `FileImport`-scoped guard) so re-imports are
idempotent. Touch points: `import_bulk` (`transactions.py`), `FileImport` (`app/models.py:69-78`),
importer base (`app/importers/base.py`).

### [x] P3 — Apply `net_delta` to `current_balance`  · *depends on P2*
Once imports are idempotent, accumulate each file's `net_delta` into `account.current_balance`
(alongside the existing snapshot path at `transactions.py:291`). This is what gives credit cards a
balance. Snapshots still win when present (authoritative).

### [ ] P4 — `balance_history` table  · *depends on P3*
New table `balance_history(id, account_id, balance, as_of, created_at)`; add a migration in
`migrate_db()` (`app/database.py`, follow the `CREATE TABLE` pattern ~lines 134-144). Log a row on
each balance change during import. Enables net-worth-over-time and growth rate later, with no rework.

### [ ] P5 — Income-by-month aggregation  · *depends on P1*
Monthly income series from the `income` bucket, grouped by year/month (mirror `summary_by_month`,
`transactions.py:145-158`, filtered to the income bucket). This is the "income history by month."

### [ ] P6 — Summary response schemas  · *convention, low effort*
Add Pydantic schemas in `app/schemas.py` (`CategorySummary`, `MonthlySummary`, `CashFlowPoint`,
`BadgeSummary`) and wire `response_model=` on endpoints. Matches the `PaginatedTransactions`
convention. (Optional but keeps the API surface consistent.)

---

## Dependency graph
```
P1 (bucketing) ──┬─► P5 (income-by-month)
                 └─► (later: cash-flow / spend charts)
P2 (dedupe) ─► P3 (apply net_delta) ─► P4 (balance_history)
                                        └─► (later: net-worth badge / over-time / growth)
P6 (schemas) — independent, do alongside anything
```

## Tests (per prerequisite)
- **P1:** `tests/test_categorizer.py` — every category mapped to exactly one bucket.
- **P2/P3:** `tests/test_import_dedupe.py` — re-import is idempotent; `net_delta` accrues on first
  import only; card balance reflects the running sum.
- **P4:** migration idempotency + a `balance_history` row logged per import.
- **P5:** income-by-month excludes spend/internal buckets; empty-month edges.
- Update the README ER diagram after `balance_history` lands (per memory rule).
- Final gate: `.venv/bin/pytest tests/ -v`.

## Verification
```bash
FARESIGHT_DB=$(mktemp --suffix=.db) uvicorn app.faresight:app --port 18765
```
Import a CapitalOne checking CSV, then a credit-card CSV; confirm both accounts have a
`current_balance`; re-import the same file and confirm the balance does **not** double.

---

## Out of scope (charting — tracked for after prerequisites land)
Do **not** start until P1–P5 are done:
- Badges row (net worth, monthly spend, income, savings rate) + `GET /api/summary/badges`
- Cash-flow chart (income vs. expense + net line) + `GET /api/summary/cashflow`
- Spending-by-category donut / month view (bucket-filtered)
- Remove the transactions table from the dashboard
- Deferred further: net worth over time, growth rate, interest widgets
