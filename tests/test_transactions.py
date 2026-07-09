"""Tests for the /api/transactions CRUD endpoints."""
import pytest

from tests.conftest import make_tx


def _make_account(client, **kwargs):
    defaults = {"bank": "Test Bank", "name": "Test Card", "account_number": "1234", "account_type": "credit_card"}
    defaults.update(kwargs)
    r = client.post("/api/accounts", json=defaults)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Create ────────────────────────────────────────────────────────────────────

def test_create_minimal(client):
    tx = make_tx(client)
    assert tx["id"] == 1
    assert tx["date"] == "2026-01-15"
    assert tx["description"] == "Test expense"
    assert tx["amount"] == -10.00
    assert tx["bank_category"] == "Uncategorized"
    assert tx["model_category"] is None
    assert tx["model_confidence"] is None  # pending (-1) masked to null
    assert tx["user_modified_category"] is False
    assert tx["account_id"] is None
    # Manual entry carries no bank reference number.
    assert tx["reference_number"] is None
    assert "created_at" in tx


def test_create_with_category_is_precategorized(client):
    """A category on manual create is a human choice: display field set, pinned, not queued."""
    tx = make_tx(client, category="Travel")
    assert tx["model_category"] == "Travel"
    assert tx["model_confidence"] == 10
    assert tx["user_modified_category"] is True
    assert tx["bank_category"] == "Uncategorized"
    # Not pending — the background categorizer must not pick it up.
    assert client.get("/api/transactions?pending_only=true").json()["total"] == 0


def test_create_without_category_is_queued(client):
    make_tx(client)
    body = client.get("/api/transactions?pending_only=true").json()
    assert body["total"] == 1


def test_create_with_explicit_bank_category_persists(client):
    tx = make_tx(client, bank_category="FOOD_AND_DRINK")
    assert tx["bank_category"] == "FOOD_AND_DRINK"
    # Bank label alone is a hint, not a categorization — row stays pending.
    assert tx["model_category"] is None
    assert tx["model_confidence"] is None


def test_create_with_all_fields(client):
    acct_id = _make_account(client, bank="Visa", name="Visa Card")
    tx = make_tx(client, account_id=acct_id, amount=-55.25)
    assert tx["account_id"] == acct_id
    assert tx["amount"] == -55.25


def test_create_positive_amount(client):
    """Positive amounts (income) are valid."""
    tx = make_tx(client, amount=1500.00, category="Salary", description="Monthly salary")
    assert tx["amount"] == 1500.00
    assert tx["model_category"] == "Salary"


def test_create_missing_required_field_returns_422(client):
    r = client.post("/api/transactions", json={"date": "2026-01-01", "amount": -5})
    assert r.status_code == 422


def test_create_invalid_date_returns_422(client):
    r = client.post(
        "/api/transactions",
        json={"date": "not-a-date", "description": "x", "amount": -1, "category": "x"},
    )
    assert r.status_code == 422


def test_create_nonexistent_file_id_returns_422(client):
    r = client.post(
        "/api/transactions",
        json={"date": "2026-01-01", "description": "x", "amount": -1, "category": "x", "file_id": 9999},
    )
    assert r.status_code == 422
    assert "FileImport" in r.json()["detail"] or "file" in r.json()["detail"].lower()


def test_create_nonexistent_account_id_returns_422(client):
    from tests.conftest import _make_file_import
    fid = _make_file_import()
    r = client.post(
        "/api/transactions",
        json={"date": "2026-01-01", "description": "x", "amount": -1, "category": "x",
              "file_id": fid, "account_id": 9999},
    )
    assert r.status_code == 422
    assert "Account" in r.json()["detail"] or "account" in r.json()["detail"].lower()


# ── Read ──────────────────────────────────────────────────────────────────────

def test_list_empty(client):
    r = client.get("/api/transactions")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["total"] == 0
    assert body["limit"] == 25
    assert body["offset"] == 0


def test_list_returns_first_page(client):
    make_tx(client, description="A")
    make_tx(client, description="B")
    body = client.get("/api/transactions").json()
    assert len(body["data"]) == 2
    assert body["total"] == 2


def test_list_ordered_by_date_desc(client):
    make_tx(client, date="2026-01-01", description="first")
    make_tx(client, date="2026-03-01", description="third")
    make_tx(client, date="2026-02-01", description="second")
    dates = [t["date"] for t in client.get("/api/transactions").json()["data"]]
    assert dates == ["2026-03-01", "2026-02-01", "2026-01-01"]


def test_list_filter_pending_only(client):
    """pending_only=true returns only rows still awaiting categorization (-1)."""
    make_tx(client, description="Pending row")                       # default model_confidence = -1
    make_tx(client, description="Done row", model_confidence=10)

    body = client.get("/api/transactions?pending_only=true").json()
    assert body["total"] == 1
    assert [t["description"] for t in body["data"]] == ["Pending row"]

    # Absent/false → unfiltered (both rows).
    assert client.get("/api/transactions").json()["total"] == 2
    assert client.get("/api/transactions?pending_only=false").json()["total"] == 2


def test_list_filter_by_account_type_credit_card(client):
    cc_id = _make_account(client, account_type="credit_card")
    bank_id = _make_account(client, account_type="checking")
    make_tx(client, account_id=cc_id, description="CC tx")
    make_tx(client, account_id=bank_id, description="Bank tx")
    body = client.get("/api/transactions?account_type=credit_card").json()
    assert body["total"] == 1
    assert body["data"][0]["description"] == "CC tx"


def test_list_filter_by_account_type_bank(client):
    cc_id = _make_account(client, account_type="credit_card")
    bank_id = _make_account(client, account_type="checking")
    make_tx(client, account_id=cc_id, description="CC tx")
    make_tx(client, account_id=bank_id, description="Bank tx")
    body = client.get("/api/transactions?account_type=bank").json()
    assert body["total"] == 1
    assert body["data"][0]["description"] == "Bank tx"


def test_list_filter_by_account_type_bank_page2_does_not_leak_cc_rows(client):
    """Regression: static ajaxParams on pagination caused page 2 to re-send credit_card."""
    cc_id = _make_account(client, account_type="credit_card")
    bank_id = _make_account(client, account_type="checking")
    for i in range(26):
        make_tx(client, account_id=bank_id, description=f"bank-{i:02d}")
    make_tx(client, account_id=cc_id, description="cc-row")
    p1 = client.get("/api/transactions?account_type=bank&page=1&limit=25").json()
    assert p1["total"] == 26
    assert len(p1["data"]) == 25
    assert all(tx["account_id"] == bank_id for tx in p1["data"])
    p2 = client.get("/api/transactions?account_type=bank&page=2&limit=25").json()
    assert len(p2["data"]) == 1
    assert p2["data"][0]["account_id"] == bank_id
    assert p2["data"][0]["description"] != "cc-row"


# ── Pagination ─────────────────────────────────────────────────────────────────

def test_list_pagination_default_limit(client):
    for i in range(30):
        make_tx(client, description=f"tx{i}")
    body = client.get("/api/transactions").json()
    assert len(body["data"]) == 25
    assert body["total"] == 30
    assert body["limit"] == 25
    assert body["offset"] == 0


def test_list_pagination_page_2(client):
    for i in range(30):
        make_tx(client, description=f"tx{i}")
    body = client.get("/api/transactions?page=2&limit=25").json()
    assert len(body["data"]) == 5
    assert body["total"] == 30
    assert body["offset"] == 25


def test_list_pagination_custom_limit(client):
    for i in range(10):
        make_tx(client, description=f"tx{i}")
    body = client.get("/api/transactions?limit=3").json()
    assert len(body["data"]) == 3
    assert body["total"] == 10
    assert body["limit"] == 3


def test_list_pagination_last_page_fewer_rows(client):
    for i in range(7):
        make_tx(client, description=f"tx{i}")
    body = client.get("/api/transactions?page=2&limit=5").json()
    assert len(body["data"]) == 2
    assert body["total"] == 7
    assert body["offset"] == 5


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
    acct_id = _make_account(client, bank="Amex", name="Amex Card")
    tx = make_tx(client)
    r = client.patch(
        f"/api/transactions/{tx['id']}",
        json={"bank_category": "Travel", "account_id": acct_id},
    )
    data = r.json()
    assert data["bank_category"] == "Travel"
    assert data["account_id"] == acct_id


def test_patch_nonexistent_returns_404(client):
    r = client.patch("/api/transactions/9999", json={"amount": -1})
    assert r.status_code == 404


# ── user_modified_category ─────────────────────────────────────────────────────

def test_user_modified_category_defaults_false(client):
    tx = make_tx(client)
    assert tx["user_modified_category"] is False


def test_patch_model_category_with_user_modified_flag(client):
    tx = make_tx(client)
    r = client.patch(
        f"/api/transactions/{tx['id']}",
        json={"model_category": "Travel", "model_confidence": 10, "user_modified_category": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["model_category"] == "Travel"
    assert data["model_confidence"] == 10
    assert data["user_modified_category"] is True


def test_patch_model_category_without_flag_leaves_it_false(client):
    tx = make_tx(client)
    r = client.patch(f"/api/transactions/{tx['id']}", json={"model_category": "Shopping"})
    assert r.status_code == 200
    data = r.json()
    assert data["model_category"] == "Shopping"
    assert data["user_modified_category"] is False


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
    remaining = client.get("/api/transactions").json()["data"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == tx1["id"]


# ── account_type filter ───────────────────────────────────────────────────────

def test_account_type_filter_credit_card(client):
    cc_id  = _make_account(client, account_type="credit_card")
    chk_id = _make_account(client, account_number="5678", account_type="checking")
    make_tx(client, account_id=cc_id,  description="cc charge")
    make_tx(client, account_id=chk_id, description="bank charge")
    make_tx(client,                    description="unlinked")

    data = client.get("/api/transactions?account_type=credit_card").json()["data"]
    assert len(data) == 1
    assert data[0]["description"] == "cc charge"


def test_account_type_filter_bank(client):
    cc_id  = _make_account(client, account_type="credit_card")
    chk_id = _make_account(client, account_number="5678", account_type="checking")
    sav_id = _make_account(client, account_number="9999", account_type="savings")
    make_tx(client, account_id=cc_id,  description="cc charge")
    make_tx(client, account_id=chk_id, description="checking tx")
    make_tx(client, account_id=sav_id, description="savings tx")

    data = client.get("/api/transactions?account_type=bank").json()["data"]
    descriptions = {tx["description"] for tx in data}
    assert descriptions == {"checking tx", "savings tx"}


def test_account_type_filter_excludes_unlinked(client):
    make_tx(client, description="no account")
    assert client.get("/api/transactions?account_type=credit_card").json()["data"] == []
    assert client.get("/api/transactions?account_type=bank").json()["data"] == []


def test_no_account_type_filter_returns_all(client):
    cc_id  = _make_account(client, account_type="credit_card")
    chk_id = _make_account(client, account_number="5678", account_type="checking")
    make_tx(client, account_id=cc_id,  description="cc")
    make_tx(client, account_id=chk_id, description="bank")
    make_tx(client,                    description="unlinked")

    total = client.get("/api/transactions").json()["total"]
    assert total == 3


def test_account_type_all_returns_everything(client):
    cc_id  = _make_account(client, account_type="credit_card")
    chk_id = _make_account(client, account_number="5678", account_type="checking")
    make_tx(client, account_id=cc_id,  description="cc")
    make_tx(client, account_id=chk_id, description="bank")
    make_tx(client,                    description="unlinked")

    total = client.get("/api/transactions?account_type=all").json()["total"]
    assert total == 3


def test_list_shows_transfer_rows(client):
    cc_id = _make_account(client, account_type="credit_card")
    make_tx(client, account_id=cc_id, description="CAPITAL ONE MOBILE PYMT",
            amount=500.00, model_category="Payments", model_confidence=10)
    make_tx(client, account_id=cc_id, description="groceries", amount=-40.00)

    descriptions = {tx["description"] for tx in client.get("/api/transactions").json()["data"]}
    assert descriptions == {"CAPITAL ONE MOBILE PYMT", "groceries"}


# ── Categorizer running ───────────────────────────────────────────────────────

def test_categorizer_running_when_proc_alive(client):
    # _FakeProc.poll() returns None (still running) by default in tests.
    r = client.get("/api/categorizer/running")
    assert r.status_code == 200
    assert r.json() == {"running": True}


def test_categorizer_not_running_when_proc_exited(client):
    from app.faresight import app as _app

    class _ExitedProc:
        def poll(self): return 0

    original = _app.state.cat_proc
    _app.state.cat_proc = _ExitedProc()
    try:
        r = client.get("/api/categorizer/running")
        assert r.status_code == 200
        assert r.json() == {"running": False}
    finally:
        _app.state.cat_proc = original


def test_categorizer_not_running_when_no_proc(client):
    from app.faresight import app as _app

    original = _app.state.cat_proc
    _app.state.cat_proc = None
    try:
        r = client.get("/api/categorizer/running")
        assert r.status_code == 200
        assert r.json() == {"running": False}
    finally:
        _app.state.cat_proc = original
