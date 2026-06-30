"""Tests for the /api/summary/* endpoints."""
from tests.conftest import make_tx


# ── /api/summary/by-category ─────────────────────────────────────────────────

def test_by_category_empty(client):
    r = client.get("/api/summary/by-category")
    assert r.status_code == 200
    assert r.json() == []


def test_by_category_sums_per_category(client):
    make_tx(client, category="Food", amount=-10.00)
    make_tx(client, category="Food", amount=-5.50)
    make_tx(client, category="Transport", amount=-20.00)

    data = {row["category"]: row["total"] for row in client.get("/api/summary/by-category").json()}

    assert data["Food"] == -15.50
    assert data["Transport"] == -20.00


def test_by_category_single_entry(client):
    make_tx(client, category="Salary", amount=3000.00)
    rows = client.get("/api/summary/by-category").json()
    assert len(rows) == 1
    assert rows[0] == {"category": "Salary", "total": 3000.00}


def test_by_category_rounds_to_two_decimals(client):
    make_tx(client, category="Misc", amount=-0.001)
    make_tx(client, category="Misc", amount=-0.001)
    rows = client.get("/api/summary/by-category").json()
    assert rows[0]["total"] == round(-0.001 + -0.001, 2)


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
