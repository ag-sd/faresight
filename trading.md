# Investments: layer trading activity into Faresight with meaningful insights

## Context

The user wants Fidelity and Betterment trading-activity exports in the app. Sample files
analyzed (`~/Downloads/transactions/Accounts_History_TEST.csv`, `betterment_2026-07-06 - TEST.csv`):
both bundle **multiple brokerage sub-accounts in one file** (Fidelity keyed by `Account Number`,
Betterment by `Account` name), and 60–70% of rows are asset movements (buys/reinvestments/rebalances)
that aren't household cash flow. The meaningful signal: **dividends/interest/cap-gains (income),
advisory+transaction fees (cost), deposits/transfers (contributions)**.

**User decisions (locked):**
1. One Faresight account per brokerage sub-account; the importer keeps only rows matching the
   target account (matched against `account.account_number`) and skips the rest. The same file is
   uploaded to each sub-account (Layer-1 dedupe is per `(account_id, content_hash)` — safe).
2. Investment income stays **out** of household cashflow/badges/savings-rate — new category bucket
   `investment`, aggregated on a new `/investments` page.
3. Trade rows are **skipped** at import (dividend+reinvestment pairs would double-count).

## Step 1 — Data model + bucket foundation

- `app/models.py`: add `investment = "investment"` to `AccountType` (SQLite Enum is VARCHAR, no
  accounts migration needed). Add `symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)`
  to `Transaction`.
- `app/database.py` `migrate_db()`: `ALTER TABLE transactions ADD COLUMN symbol VARCHAR(20)` (guarded
  on column existence, per existing pattern); idempotent `INSERT OR IGNORE` block for the new
  categories on existing DBs (`sort_order` continuing from `MAX(sort_order)`).
- `app/category_defaults.py`: append —
  `Dividends` (investment), `Investment Interest` (investment — named to avoid colliding with the
  existing income-bucket `Interest Income`), `Capital Gains` (investment),
  `Investment Fees` (investment — fees deducted inside the portfolio must not hit household spend,
  symmetric with income exclusion), `Contributions` (**internal** — deposits/EFT/ACATS/Roth
  conversions; the checking-side outflow is already internal).
- `app/schemas.py`: `VALID_BUCKETS` += `"investment"`; `TransactionCreate.symbol: Optional[str] = None`
  (flows through import_bulk's `model_dump()` and `TransactionOut` automatically). Use `Optional[T]`,
  never `T | None` (Py 3.14 Pydantic bug).
- `app/buckets.py`: add `HIDDEN_BUCKETS = ("internal", "investment")` + `hidden_names(db)` helper.

## Step 2 — Bucket exclusion sweep (`app/routers/transactions.py`, `app/routers/insights.py`)

Verified leak inventory:

| Query | Shape | Action |
|---|---|---|
| `/api/summary/by-month` (no bucket), `/api/summary/by-category-for-period` | `_exclude_internal` ("all but internal") | **FIX**: generalize `_exclude_internal` → `_exclude_hidden(q, db)` using `hidden_names()` (same NULL-safe `or_` shape) |
| `/api/insights/recurring` | inline internal-only `notin_` on `amount < 0` | **FIX**: use `hidden_names()` — else monthly Advisory Fees show as "subscriptions" |
| cashflow/badges (`_flow_sums`), `by-month?bucket=`, category-trends, top-merchants | strict income/spend name membership | no change — investment rows are always pre-categorized; **regression tests only** |
| `/api/summary/by-model-category` | no filter, no frontend consumer | leave as-is |

Also `_filter_by_account_type` (transactions.py:28): add explicit `investment` branch **before** the
bank fallback (currently any non-credit_card value falls through to checking+savings).

## Step 3 — Categorizer exclusion (`app/categorizer.py`)

`_load_category_data`: add `.filter(Category.bucket != "investment")` so the LLM never labels bank
rows as Dividends; a hallucinated investment label falls back to `Other` via the canonical-map miss.

## Step 4 — Fidelity importer (`app/importers/fidelity.py` + registry)

`FidelityInvestment(CsvImporter[dict])`, registered as `"Fidelity Investments"`:
- `skip_lines() = 2`, `row_start() = 4` (2 blank lines precede the header; DictReader can't
  self-recover — verified). Trailing disclaimer/footer rows: empty `Account Number` or empty
  `Amount ($)` → `return None`.
- Row filter: `row["Account Number"].strip() != account.account_number.strip()` → skip.
- Action-prefix mapping (case-insensitive, ordered):

| Action prefix | → |
|---|---|
| `DIVIDEND RECEIVED` | Dividends |
| `INTEREST` / `INTEREST EARNED` | Investment Interest |
| `LONG-TERM CAP GAIN`, `SHORT-TERM CAP GAIN` | Capital Gains |
| `ADVISOR FEE`, `FEE CHARGED`, `FOREIGN TAX PAID` | Investment Fees |
| `ELECTRONIC FUNDS TRANSFER`, `DIRECT DEPOSIT`, `CONV TO ROTH`, `ROTH CONVERSION` | Contributions |
| `REINVESTMENT`, `YOU BOUGHT*`, `YOU SOLD`, `REDEMPTION PAYOUT` | skip (intra-account asset movement — a maturing CD is not a contribution) |
| anything else | `raise ValueError(f"unknown action: ...")` — new verbs surface as row errors, not silent loss |

- `TransactionCreate(..., description=action, amount=float(signed Amount), symbol=Symbol or None,
  model_category=<mapped>, model_confidence=10)` — pre-categorized, never queued for the LLM.
  Dates `%m/%d/%Y`. No snapshot, no reference_number.

## Step 5 — Betterment importer (`app/importers/betterment.py` + registry)

`BettermentInvestment(CsvImporter[dict])`, registered as `"Betterment"`; clean header.
- Sub-account filter: `row["Account"].strip() != account.account_number.strip()` (the Faresight
  account_number holds the Betterment sub-account name, e.g. `Individual Taxable`).
- `_parse_amount`: `""`/`"—"` → None (skip row); else `float(s.replace("$","").replace(",",""))`
  — handles `$1,234.56` / `-$75.00`.
- Description mapping: `Trade Activity`, `Portfolio Change`, `*Stock Split` → skip;
  `Dividend Payment` → Dividends; `Advisory Fee`, `Outbound Transfer Fee` → Investment Fees;
  `Deposit*`/`Recurring Deposit`, `*ACATS Transfer*`, `Conversion*`, `Transfer*`,
  `*Tax Year Contribution`, `Withdrawal*` → Contributions; else `ValueError`.
- `symbol = Security` (`-`/empty → None); ISO dates; pre-categorized confidence 10.

## Step 6 — Balance handling

- `import_bulk` (transactions.py:~491): gate the delta accumulation —
  `elif to_insert and account.account_type != AccountType.investment:` — cash-flow deltas cannot
  represent a market-driven portfolio value; balance is manual for investment accounts.
- `AccountUpdate` (schemas.py): add `current_balance: Optional[float] = None`; in `update_account`
  (accounts.py), when set, also log a `BalanceHistory` row (`as_of=date.today()`) so net-worth
  history stays coherent.

## Step 7 — Investments summary API (new `app/routers/investments.py`)

One composite endpoint `GET /api/investments/summary` (all queries join Account,
`account_type == investment`, membership via `bucket_names()`):

```
income_by_month:        [{year, month, category, total}]   # investment bucket, amount > 0
fees_by_month:          [MonthlySummary]                    # investment bucket, amount < 0
contributions_by_month: [MonthlySummary]                    # internal bucket on investment accounts
top_symbols:            [{symbol, total}]                   # amount > 0, symbol not null, limit=10
```

New Pydantic schemas in `app/schemas.py`; router included in `app/faresight.py`.

## Step 8 — Frontend: /investments page

**Reuse `account_page.html` conditionally** (the INCOME_CTX/EXPENSES_CTX pattern exists for this):
- `app/faresight.py`: `INVESTMENTS_CTX` (`active_page="investments"`, `scope="investment"`,
  `show_transfers=False`, `show_investment_insights=True`, add/type options `investment`) +
  `GET /investments` route. Nav link in `frontend/app/layouts/base.html`.
- `account_page.html`:
  - `{% if show_investment_insights %}` insights section (between accounts accordion and activity
    card): income-by-month stacked bar (per-category `categoryColor()`), fees bar, contributions
    bar, top-symbols list (dashboard `topMerchantsList` pattern); Chart.js CDN tag + new
    `frontend/assets/scripts/investments.js` loaded conditionally.
  - `{% if scope == 'investment' %}` `current_balance` field in Add + Details modals (manual
    portfolio-value refresh; hint text on account number: "Fidelity: account number ·
    Betterment: sub-account name exactly as exported").
- `accounts.js`: `inScope()` investment branch, `ACCOUNT_TYPE_LABELS`/`openAddAccount` entries,
  send `current_balance` from the modals when the field exists.
- `common.js` `txColumns()`: optional Symbol column (flag via PAGE_CONFIG, on only for /investments).
- Bucket dropdowns gain `investment`: `upload.js` categories-table editor values + `upload.html`
  `#catBucket`.

## Step 9 — Tests + docs

- **Importer units** (`tests/test_fidelity.py`, `tests/test_betterment.py` + trimmed fixture CSVs
  `tests/fidelity_sample.csv`, `tests/betterment_sample.csv` covering every observed row type,
  ≥2 sub-accounts, footer/disclaimer, negative fees, em-dash amounts): registry entry; only
  target-account rows import; trades skipped errorlessly; action→category+sign; symbol capture;
  confidence 10; unknown action → `Row {n}` error.
- **Integration** (`tests/test_investment_import.py`): same bytes uploaded to two investment
  accounts → each gets only its rows; manually-set `current_balance` untouched by import;
  checking-account accumulation regression; re-upload → `duplicate_file`; rows never pending.
- **Exclusion regressions** (`tests/test_investment_exclusion.py`): seeded Dividends/Investment
  Fees/Contributions rows absent from cashflow, badges income/spend/savings-rate, by-month (±bucket),
  by-category-for-period, recurring (repeated −$75 Advisory Fee must NOT appear), trends,
  top-merchants; badges `assets` DOES include investment `current_balance`;
  `account_type=investment` filter works and `bank` excludes it.
- **API/page tests**: `/api/investments/summary` shapes incl. empty DB; `tests/test_pages.py` —
  /investments serves, PAGE_CONFIG scope, insight element IDs, balance field only on /investments,
  nav-link loops updated.
- **Categorizer**: `_load_category_data` excludes investment names.
- README: ER diagram (`symbol`, bucket comment), API table, page list. Full `pytest` as final gate.

## Verification

1. Full suite green: `.venv/bin/pytest tests/ -v`.
2. Manual smoke on a temp DB (`FARESIGHT_DB=$(mktemp --suffix=.db) uvicorn app.faresight:app --port 18765`):
   create investment accounts for two Fidelity sub-accounts (numbers `655245737`, `X73881710`,
   importer "Fidelity Investments") and one Betterment ("Individual Taxable", importer "Betterment");
   upload the real sample files; verify each account received only its rows, dividends/fees/
   contributions charts populate on /investments, dashboard income/spend/savings-rate unchanged,
   net worth includes manually-entered balances.

## Risks / notes

- Betterment matching relies on the user entering the sub-account name in the account-number field —
  mitigated with placeholder hint text.
- Fidelity's 2-blank-line preamble is hardcoded (`skip_lines=2`); if Fidelity changes it the failure
  is loud (zero rows/all errors), not silent.
- Unmapped actions/descriptions raise per-row errors by design — new bank verbs surface in the
  upload result modal instead of dropping cash flows.
- Rules retro-apply may overwrite importer pre-categorization (guarded only on
  `user_modified_category`) — consistent with existing precedence, left as-is.
