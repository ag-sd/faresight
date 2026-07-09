"""Tests for the /api/summary/* endpoints."""
from tests.conftest import make_tx


# ── /api/summary/by-model-category ──────────────────────────────────────────

def test_by_model_category_empty(client):
    r = client.get("/api/summary/by-model-category")
    assert r.status_code == 200
    assert r.json() == []


def test_by_model_category_sums_per_category(client):
    make_tx(client, model_category="Groceries", model_confidence=8, amount=-10.00)
    make_tx(client, model_category="Groceries", model_confidence=7, amount=-5.00)
    make_tx(client, model_category="Shopping",  model_confidence=6, amount=-20.00)

    data = {r["category"]: r["total"] for r in client.get("/api/summary/by-model-category").json()}

    assert data["Groceries"] == -15.00
    assert data["Shopping"] == -20.00


def test_by_model_category_excludes_pending(client):
    make_tx(client, model_category=None, model_confidence=-1, amount=-50.00)
    r = client.get("/api/summary/by-model-category")
    assert r.json() == []


def test_by_model_category_excludes_null_category(client):
    make_tx(client, model_category=None, model_confidence=None, amount=-50.00)
    r = client.get("/api/summary/by-model-category")
    assert r.json() == []


# ── /api/summary/by-month ─────────────────────────────────────────────────────

def test_by_month_empty(client):
    r = client.get("/api/summary/by-month")
    assert r.status_code == 200
    assert r.json() == []


def test_by_month_groups_correctly(client):
    make_tx(client, date="2026-01-10", amount=-100.00)
    make_tx(client, date="2026-01-20", amount=-50.00)
    make_tx(client, date="2026-02-05", amount=-200.00)

    rows = client.get("/api/summary/by-month").json()
    by_month = {(r["year"], r["month"]): r["total"] for r in rows}

    assert by_month[(2026, 1)] == -150.00
    assert by_month[(2026, 2)] == -200.00


def test_by_month_ordered_chronologically(client):
    make_tx(client, date="2026-03-01", amount=-1)
    make_tx(client, date="2026-01-01", amount=-1)
    make_tx(client, date="2026-02-01", amount=-1)

    rows = client.get("/api/summary/by-month").json()
    months = [(r["year"], r["month"]) for r in rows]
    assert months == sorted(months)


def test_by_month_spans_multiple_years(client):
    make_tx(client, date="2025-12-31", amount=-10.00)
    make_tx(client, date="2026-01-01", amount=-20.00)

    rows = client.get("/api/summary/by-month").json()
    by_ym = {(r["year"], r["month"]): r["total"] for r in rows}

    assert (2025, 12) in by_ym
    assert (2026, 1) in by_ym


# ── /api/summary/by-category-for-period ──────────────────────────────────────

def test_by_category_for_period_empty(client):
    r = client.get("/api/summary/by-category-for-period?year=2026")
    assert r.status_code == 200
    assert r.json() == []


def test_by_category_for_period_year_only(client):
    make_tx(client, date="2026-01-10", model_category="Groceries", model_confidence=8, amount=-10.00)
    make_tx(client, date="2026-06-15", model_category="Groceries", model_confidence=7, amount=-5.00)
    make_tx(client, date="2026-03-01", model_category="Shopping",  model_confidence=6, amount=-20.00)

    data = {r["category"]: r["total"] for r in client.get("/api/summary/by-category-for-period?year=2026").json()}

    assert data["Groceries"] == -15.00
    assert data["Shopping"] == -20.00


def test_by_category_for_period_with_month(client):
    make_tx(client, date="2026-01-10", model_category="Groceries", model_confidence=8, amount=-10.00)
    make_tx(client, date="2026-02-15", model_category="Groceries", model_confidence=7, amount=-5.00)

    data = {r["category"]: r["total"] for r in client.get("/api/summary/by-category-for-period?year=2026&month=1").json()}

    assert data["Groceries"] == -10.00
    assert "Groceries" in data
    assert len(data) == 1


def test_by_category_for_period_excludes_other_years(client):
    make_tx(client, date="2025-12-31", model_category="Travel", model_confidence=9, amount=-100.00)
    make_tx(client, date="2026-01-01", model_category="Travel", model_confidence=9, amount=-50.00)

    data = {r["category"]: r["total"] for r in client.get("/api/summary/by-category-for-period?year=2026").json()}

    assert data.get("Travel") == -50.00


def test_by_category_for_period_excludes_pending(client):
    make_tx(client, date="2026-05-01", model_category=None, model_confidence=-1, amount=-50.00)
    r = client.get("/api/summary/by-category-for-period?year=2026")
    assert r.json() == []


def test_by_category_for_period_missing_year_returns_422(client):
    r = client.get("/api/summary/by-category-for-period")
    assert r.status_code == 422


# ── account_type filtering ────────────────────────────────────────────────────

def _make_account(client, account_type="credit_card", account_number="1111"):
    r = client.post("/api/accounts", json={
        "bank": "Test", "name": "Acct", "account_number": account_number,
        "account_type": account_type,
    })
    assert r.status_code == 201
    return r.json()["id"]


def test_by_month_filters_by_credit_card(client):
    cc_id  = _make_account(client, "credit_card", "1111")
    chk_id = _make_account(client, "checking",    "2222")
    make_tx(client, date="2026-01-01", amount=-100.00, account_id=cc_id)
    make_tx(client, date="2026-01-01", amount=-200.00, account_id=chk_id)

    rows = client.get("/api/summary/by-month?account_type=credit_card").json()
    assert len(rows) == 1
    assert rows[0]["total"] == -100.00


def test_by_month_filters_by_bank(client):
    cc_id  = _make_account(client, "credit_card", "1111")
    chk_id = _make_account(client, "checking",    "2222")
    sav_id = _make_account(client, "savings",     "3333")
    make_tx(client, date="2026-02-01", amount=-50.00,  account_id=cc_id)
    make_tx(client, date="2026-02-01", amount=-150.00, account_id=chk_id)
    make_tx(client, date="2026-02-01", amount=-250.00, account_id=sav_id)

    rows = client.get("/api/summary/by-month?account_type=bank").json()
    assert len(rows) == 1
    assert rows[0]["total"] == -400.00


def test_by_category_for_period_filters_by_credit_card(client):
    cc_id  = _make_account(client, "credit_card", "1111")
    chk_id = _make_account(client, "checking",    "2222")
    make_tx(client, date="2026-03-01", model_category="Dining & Takeout", model_confidence=8,
            amount=-30.00, account_id=cc_id)
    make_tx(client, date="2026-03-01", model_category="Dining & Takeout", model_confidence=8,
            amount=-70.00, account_id=chk_id)

    data = {r["category"]: r["total"]
            for r in client.get("/api/summary/by-category-for-period?year=2026&account_type=credit_card").json()}
    assert data.get("Dining & Takeout") == -30.00


def test_by_category_for_period_filters_by_bank(client):
    cc_id  = _make_account(client, "credit_card", "1111")
    sav_id = _make_account(client, "savings",     "3333")
    make_tx(client, date="2026-04-01", model_category="Groceries", model_confidence=9,
            amount=-40.00, account_id=cc_id)
    make_tx(client, date="2026-04-01", model_category="Groceries", model_confidence=9,
            amount=-60.00, account_id=sav_id)

    data = {r["category"]: r["total"]
            for r in client.get("/api/summary/by-category-for-period?year=2026&account_type=bank").json()}
    assert data.get("Groceries") == -60.00


# ── transfer/payment exclusion from spending aggregates ──────────────────────

def _make_double_count_scenario(client):
    """The canonical double-count: a CC bill payment appears on both sides.

    CC:       Groceries -100, payment credit +500 (Payments)
    Checking: payment debit -500 (Payments), Income +2000
    True combined total for the month: -100 + 2000 = 1900.
    """
    cc_id  = _make_account(client, "credit_card", "1111")
    chk_id = _make_account(client, "checking",    "2222")
    make_tx(client, date="2026-01-05", amount=-100.00, account_id=cc_id,
            model_category="Groceries", model_confidence=8)
    make_tx(client, date="2026-01-10", amount=500.00, account_id=cc_id,
            model_category="Payments", model_confidence=10)
    make_tx(client, date="2026-01-10", amount=-500.00, account_id=chk_id,
            model_category="Payments", model_confidence=10)
    make_tx(client, date="2026-01-15", amount=2000.00, account_id=chk_id,
            model_category="Income", model_confidence=9)


def test_by_month_excludes_transfers_combined(client):
    _make_double_count_scenario(client)
    for qs in ("", "?account_type=all"):
        rows = client.get(f"/api/summary/by-month{qs}").json()
        assert len(rows) == 1
        assert rows[0]["total"] == 1900.00


def test_by_month_excludes_transfers_per_type(client):
    _make_double_count_scenario(client)
    cc = client.get("/api/summary/by-month?account_type=credit_card").json()
    assert len(cc) == 1
    assert cc[0]["total"] == -100.00
    bank = client.get("/api/summary/by-month?account_type=bank").json()
    assert len(bank) == 1
    assert bank[0]["total"] == 2000.00


def test_by_month_keeps_null_model_category(client):
    # NULL-safety: pending/uncategorized rows must not vanish from summaries.
    make_tx(client, date="2026-03-01", amount=-25.00,
            model_category=None, model_confidence=-1)
    rows = client.get("/api/summary/by-month").json()
    assert len(rows) == 1
    assert rows[0]["total"] == -25.00


def test_by_month_includes_unlinked(client):
    make_tx(client, date="2026-04-01", amount=-10.00)
    assert client.get("/api/summary/by-month?account_type=all").json()[0]["total"] == -10.00
    assert client.get("/api/summary/by-month?account_type=credit_card").json() == []
    assert client.get("/api/summary/by-month?account_type=bank").json() == []


def test_by_category_for_period_excludes_transfers(client):
    make_tx(client, date="2026-05-01", amount=-80.00,
            model_category="Groceries", model_confidence=9)
    make_tx(client, date="2026-05-02", amount=-500.00,
            model_category="Payments", model_confidence=10)
    make_tx(client, date="2026-05-03", amount=-12.00,
            model_category="Transfers", model_confidence=10)

    for qs in ("", "&account_type=all"):
        data = {r["category"]: r["total"]
                for r in client.get(f"/api/summary/by-category-for-period?year=2026{qs}").json()}
        assert data == {"Groceries": -80.00}


def test_by_category_for_period_counts_fees_and_interest(client):
    # The old "Transfers & Fees" bucket was split: only Transfers is money movement.
    # Fees and Interest Paid are real spending; Interest Income is real income —
    # all three must appear in the summary (unlike the excluded Transfers/Payments).
    make_tx(client, date="2026-05-01", amount=-80.00,
            model_category="Groceries", model_confidence=9)
    make_tx(client, date="2026-05-02", amount=-12.00,
            model_category="Fees", model_confidence=10)
    make_tx(client, date="2026-05-03", amount=-30.00,
            model_category="Interest Paid", model_confidence=10)
    make_tx(client, date="2026-05-04", amount=5.00,
            model_category="Interest Income", model_confidence=10)
    make_tx(client, date="2026-05-05", amount=-99.00,
            model_category="Transfers", model_confidence=10)

    data = {r["category"]: r["total"]
            for r in client.get("/api/summary/by-category-for-period?year=2026").json()}
    assert data == {
        "Groceries": -80.00,
        "Fees": -12.00,
        "Interest Paid": -30.00,
        "Interest Income": 5.00,
    }


# ── P6: response_model shape conformance ──────────────────────────────────────
# response_model= coerces the returned dicts; these lock the exact keys/types so a
# future field rename can't silently drift the API surface.

def test_model_category_summary_shape(client):
    make_tx(client, model_category="Groceries", model_confidence=8, amount=-9.00, date="2026-01-05")
    for path in ("/api/summary/by-model-category", "/api/summary/by-category-for-period?year=2026"):
        row = client.get(path).json()[0]
        assert set(row) == {"category", "total"}
        assert isinstance(row["category"], str)
        assert isinstance(row["total"], float)


def test_monthly_summary_shape(client):
    make_tx(client, date="2026-01-10", amount=-100.00)
    row = client.get("/api/summary/by-month").json()[0]
    assert set(row) == {"year", "month", "total"}
    assert isinstance(row["year"], int)
    assert isinstance(row["month"], int)
    assert isinstance(row["total"], float)


# ── P5: /api/summary/by-month?bucket= ─────────────────────────────────────────

def _seed_mixed_month(client):
    """One month of income / spend / internal / uncategorized rows."""
    make_tx(client, date="2026-01-05", amount=3000.00,
            model_category="Income", model_confidence=9, description="Payroll")
    make_tx(client, date="2026-01-31", amount=25.00,
            model_category="Interest Income", model_confidence=9, description="Interest")
    make_tx(client, date="2026-01-10", amount=-80.00,
            model_category="Groceries", model_confidence=8, description="Market")
    make_tx(client, date="2026-01-15", amount=-500.00,
            model_category="Payments", model_confidence=10, description="CC payment")
    make_tx(client, date="2026-01-20", amount=-40.00, description="Uncategorized")


def test_by_month_income_bucket_only_income_rows(client):
    _seed_mixed_month(client)
    rows = client.get("/api/summary/by-month?bucket=income").json()
    assert rows == [{"year": 2026, "month": 1, "total": 3025.00}]


def test_by_month_income_bucket_excludes_uncategorized(client):
    # Strict: a NULL model_category row is never income.
    make_tx(client, date="2026-01-05", amount=1000.00, description="Mystery deposit")
    assert client.get("/api/summary/by-month?bucket=income").json() == []


def test_by_month_spend_bucket_keeps_null_excludes_income_and_internal(client):
    _seed_mixed_month(client)
    rows = client.get("/api/summary/by-month?bucket=spend").json()
    # Groceries −80 + uncategorized −40; income and Payments excluded.
    assert rows == [{"year": 2026, "month": 1, "total": -120.00}]


def test_by_month_custom_income_category_is_picked_up(client):
    client.post("/api/categories", json={
        "name": "Dividends", "color": "#aabbcc", "bucket": "income",
    })
    make_tx(client, date="2026-03-01", amount=55.00,
            model_category="Dividends", model_confidence=9)
    rows = client.get("/api/summary/by-month?bucket=income").json()
    assert rows == [{"year": 2026, "month": 3, "total": 55.00}]


def test_by_month_invalid_bucket_422(client):
    assert client.get("/api/summary/by-month?bucket=bogus").status_code == 422


def test_by_month_income_bucket_spans_months(client):
    make_tx(client, date="2026-01-05", amount=3000.00,
            model_category="Income", model_confidence=9, description="Jan payroll")
    make_tx(client, date="2026-02-05", amount=3100.00,
            model_category="Income", model_confidence=9, description="Feb payroll")
    rows = client.get("/api/summary/by-month?bucket=income").json()
    assert [(r["month"], r["total"]) for r in rows] == [(1, 3000.00), (2, 3100.00)]


# ── /api/summary/cashflow ─────────────────────────────────────────────────────

def test_cashflow_empty(client):
    r = client.get("/api/summary/cashflow")
    assert r.status_code == 200
    assert r.json() == []


def test_cashflow_math_per_month(client):
    _seed_mixed_month(client)
    rows = client.get("/api/summary/cashflow").json()
    assert rows == [{
        "year": 2026, "month": 1,
        "income": 3025.00,          # Payroll + Interest
        "spend": -120.00,           # Groceries + uncategorized; Payments excluded
        "net": 2905.00,
    }]


def test_cashflow_multiple_months_ordered(client):
    make_tx(client, date="2025-12-05", amount=2000.00,
            model_category="Income", model_confidence=9, description="Dec payroll")
    make_tx(client, date="2026-01-10", amount=-50.00,
            model_category="Groceries", model_confidence=8)
    rows = client.get("/api/summary/cashflow").json()
    assert [(r["year"], r["month"]) for r in rows] == [(2025, 12), (2026, 1)]
    assert rows[0]["income"] == 2000.00 and rows[0]["spend"] == 0.00
    assert rows[1]["income"] == 0.00 and rows[1]["spend"] == -50.00


def test_cashflow_internal_only_month_nets_zero(client):
    make_tx(client, date="2026-04-01", amount=500.00,
            model_category="Payments", model_confidence=10)
    rows = client.get("/api/summary/cashflow").json()
    # The month still appears (a transaction exists) but contributes nothing.
    assert rows == [{"year": 2026, "month": 4, "income": 0.00, "spend": 0.00, "net": 0.00}]


def test_cashflow_point_shape(client):
    make_tx(client, date="2026-01-10", amount=-100.00)
    row = client.get("/api/summary/cashflow").json()[0]
    assert set(row) == {"year", "month", "income", "spend", "net"}
    assert isinstance(row["year"], int)
    assert isinstance(row["month"], int)
    for f in ("income", "spend", "net"):
        assert isinstance(row[f], float)
