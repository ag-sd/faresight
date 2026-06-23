"""Tests for the /api/transactions CRUD endpoints."""
import pytest

from tests.conftest import make_tx


# ── Create ────────────────────────────────────────────────────────────────────

def test_create_minimal(client):
    tx = make_tx(client)
    assert tx["id"] == 1
    assert tx["date"] == "2026-01-15"
    assert tx["description"] == "Test expense"
    assert tx["amount"] == -10.00
    assert tx["category"] == "Food"
    assert tx["note"] is None
    assert tx["source"] is None
    assert "created_at" in tx


def test_create_with_all_fields(client):
    tx = make_tx(client, note="weekly shop", source="Visa", amount=-55.25)
    assert tx["note"] == "weekly shop"
    assert tx["source"] == "Visa"
    assert tx["amount"] == -55.25


def test_create_positive_amount(client):
    """Positive amounts (income) are valid."""
    tx = make_tx(client, amount=1500.00, category="Salary", description="Monthly salary")
    assert tx["amount"] == 1500.00


def test_create_missing_required_field_returns_422(client):
    r = client.post("/api/transactions", json={"date": "2026-01-01", "amount": -5})
    assert r.status_code == 422


def test_create_invalid_date_returns_422(client):
    r = client.post(
        "/api/transactions",
        json={"date": "not-a-date", "description": "x", "amount": -1, "category": "x"},
    )
    assert r.status_code == 422


# ── Read ──────────────────────────────────────────────────────────────────────

def test_list_empty(client):
    r = client.get("/api/transactions")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_all(client):
    make_tx(client, description="A")
    make_tx(client, description="B")
    txs = client.get("/api/transactions").json()
    assert len(txs) == 2


def test_list_ordered_by_date_desc(client):
    make_tx(client, date="2026-01-01", description="first")
    make_tx(client, date="2026-03-01", description="third")
    make_tx(client, date="2026-02-01", description="second")
    dates = [t["date"] for t in client.get("/api/transactions").json()]
    assert dates == ["2026-03-01", "2026-02-01", "2026-01-01"]


def test_list_filter_by_category(client):
    make_tx(client, category="Food")
    make_tx(client, category="Transport")
    make_tx(client, category="Food")
    r = client.get("/api/transactions?category=Food")
    assert r.status_code == 200
    assert all(t["category"] == "Food" for t in r.json())
    assert len(r.json()) == 2


def test_list_filter_unknown_category_returns_empty(client):
    make_tx(client, category="Food")
    r = client.get("/api/transactions?category=NonExistent")
    assert r.status_code == 200
    assert r.json() == []


def test_get_one(client):
    tx = make_tx(client)
    r = client.get(f"/api/transactions/{tx['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == tx["id"]


def test_get_nonexistent_returns_404(client):
    r = client.get("/api/transactions/9999")
    assert r.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────

def test_patch_description(client):
    tx = make_tx(client)
    r = client.patch(f"/api/transactions/{tx['id']}", json={"description": "Updated"})
    assert r.status_code == 200
    assert r.json()["description"] == "Updated"
    assert r.json()["amount"] == tx["amount"]  # unchanged


def test_patch_amount(client):
    tx = make_tx(client)
    r = client.patch(f"/api/transactions/{tx['id']}", json={"amount": -99.99})
    assert r.status_code == 200
    assert r.json()["amount"] == -99.99


def test_patch_multiple_fields(client):
    tx = make_tx(client)
    r = client.patch(
        f"/api/transactions/{tx['id']}",
        json={"category": "Travel", "note": "flight", "source": "Amex"},
    )
    data = r.json()
    assert data["category"] == "Travel"
    assert data["note"] == "flight"
    assert data["source"] == "Amex"


def test_patch_nonexistent_returns_404(client):
    r = client.patch("/api/transactions/9999", json={"amount": -1})
    assert r.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete(client):
    tx = make_tx(client)
    r = client.delete(f"/api/transactions/{tx['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/transactions/{tx['id']}").status_code == 404


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/api/transactions/9999")
    assert r.status_code == 404


def test_delete_does_not_affect_other_transactions(client):
    tx1 = make_tx(client, description="keep")
    tx2 = make_tx(client, description="remove")
    client.delete(f"/api/transactions/{tx2['id']}")
    remaining = client.get("/api/transactions").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == tx1["id"]
