"""Tests for GET /api/summary/badges — net worth + monthly flow + savings rate.

Month-scoped assertions pass explicit ?year=&month= so they are deterministic;
only the default-params smoke test touches date.today().
"""
from tests.conftest import make_tx


def _make_account(client, account_type="checking", name="Checking", balance=None):
    r = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": name,
        "account_number": "0001",
        "account_type": account_type,
        "current_balance": balance,
        "default_importer": "Capital One Credit Card",
    })
    assert r.status_code == 201, r.text
    return r.json()


def _badges(client, year=None, month=None):
    qs = f"?year={year}&month={month}" if year is not None else ""
    r = client.get(f"/api/summary/badges{qs}")
    assert r.status_code == 200, r.text
    return r.json()


# ── Net worth ─────────────────────────────────────────────────────────────────

def test_net_worth_sums_assets_and_liabilities(client):
    _make_account(client, "savings", "Savings", 10000.00)
    _make_account(client, "credit_card", "Card", -750.50)
    b = _badges(client)
    assert b["assets"] == 10000.00
    assert b["liabilities"] == -750.50
    assert b["net_worth"] == 9249.50


def test_net_worth_skips_null_balances(client):
    _make_account(client, "savings", "Savings", 5000.00)
    _make_account(client, "checking", "No balance yet", None)
    assert _badges(client)["net_worth"] == 5000.00


def test_net_worth_excludes_inactive_accounts(client):
    _make_account(client, "savings", "Savings", 5000.00)
    stale = _make_account(client, "credit_card", "Old card", -900.00)
    client.patch(f"/api/accounts/{stale['id']}", json={"is_active": False})
    b = _badges(client)
    assert b["liabilities"] == 0.00
    assert b["net_worth"] == 5000.00


def test_net_worth_zero_with_no_accounts(client):
    b = _badges(client)
    assert b["net_worth"] == 0.00
    assert b["assets"] == 0.00
    assert b["liabilities"] == 0.00


# ── Monthly flow ──────────────────────────────────────────────────────────────

def test_month_flow_counts_only_requested_month(client):
    make_tx(client, date="2026-05-05", amount=3000.00,
            model_category="Income", model_confidence=9, description="May payroll")
    make_tx(client, date="2026-05-10", amount=-200.00,
            model_category="Groceries", model_confidence=8, description="May food")
    make_tx(client, date="2026-06-05", amount=9999.00,
            model_category="Income", model_confidence=9, description="June payroll")

    b = _badges(client, 2026, 5)
    assert b["month_income"] == 3000.00
    assert b["month_spend"] == -200.00


def test_prev_month_values(client):
    make_tx(client, date="2026-04-05", amount=2500.00,
            model_category="Income", model_confidence=9, description="Apr payroll")
    make_tx(client, date="2026-04-12", amount=-100.00,
            model_category="Groceries", model_confidence=8, description="Apr food")
    b = _badges(client, 2026, 5)
    assert b["prev_month_income"] == 2500.00
    assert b["prev_month_spend"] == -100.00


def test_january_rolls_prev_month_to_december(client):
    make_tx(client, date="2025-12-05", amount=2000.00,
            model_category="Income", model_confidence=9, description="Dec payroll")
    b = _badges(client, 2026, 1)
    assert b["prev_month_income"] == 2000.00


def test_month_flow_excludes_internal_and_strict_income(client):
    make_tx(client, date="2026-05-01", amount=500.00,
            model_category="Payments", model_confidence=10, description="CC payment")
    make_tx(client, date="2026-05-02", amount=1000.00, description="Uncat deposit")
    b = _badges(client, 2026, 5)
    assert b["month_income"] == 0.00        # uncategorized is never income
    assert b["month_spend"] == 1000.00      # NULL-safe: uncategorized counts as spend


def test_invalid_month_422(client):
    assert client.get("/api/summary/badges?year=2026&month=13").status_code == 422


# ── Savings rate ──────────────────────────────────────────────────────────────

def test_savings_rate_math(client):
    make_tx(client, date="2026-05-05", amount=4000.00,
            model_category="Income", model_confidence=9)
    make_tx(client, date="2026-05-10", amount=-1000.00,
            model_category="Groceries", model_confidence=8)
    # (4000 − 1000) / 4000
    assert _badges(client, 2026, 5)["savings_rate"] == 0.75


def test_savings_rate_null_without_income(client):
    make_tx(client, date="2026-05-10", amount=-1000.00,
            model_category="Groceries", model_confidence=8)
    assert _badges(client, 2026, 5)["savings_rate"] is None


def test_savings_rate_negative_when_overspending(client):
    make_tx(client, date="2026-05-05", amount=1000.00,
            model_category="Income", model_confidence=9)
    make_tx(client, date="2026-05-10", amount=-1500.00,
            model_category="Groceries", model_confidence=8)
    assert _badges(client, 2026, 5)["savings_rate"] == -0.5


# ── Defaults + shape ─────────────────────────────────────────────────────────

def test_default_params_smoke(client):
    b = _badges(client)  # today's month; just verify it answers with the full shape
    assert set(b) == {
        "net_worth", "assets", "liabilities",
        "month_income", "month_spend",
        "prev_month_income", "prev_month_spend",
        "savings_rate",
    }
