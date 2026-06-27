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
