import pytest


def _make_account(client, **kwargs):
    defaults = {
        "bank": "Chase",
        "name": "My Chase Card",
        "account_number": "1234",
        "account_type": "credit_card",
        "notes": None,
    }
    defaults.update(kwargs)
    r = client.post("/api/accounts", json=defaults)
    assert r.status_code == 201, r.text
    return r.json()


# ── GET /api/accounts ─────────────────────────────────────────────────────────

def test_list_accounts_empty(client):
    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert r.json() == []


def test_list_accounts_returns_all(client):
    _make_account(client, account_type="checking")
    _make_account(client, account_type="savings")
    accounts = client.get("/api/accounts").json()
    assert len(accounts) == 2


# ── POST /api/accounts ────────────────────────────────────────────────────────

def test_create_credit_card(client):
    a = _make_account(client, account_type="credit_card")
    assert a["account_type"] == "credit_card"
    assert a["is_active"] is True
    assert "id" in a
    assert "created_at" in a


def test_create_checking(client):
    a = _make_account(client, account_type="checking", name="Main Checking")
    assert a["account_type"] == "checking"
    assert a["name"] == "Main Checking"


def test_create_savings(client):
    a = _make_account(client, account_type="savings")
    assert a["account_type"] == "savings"


def test_create_invalid_account_type_returns_422(client):
    r = client.post("/api/accounts", json={
        "bank": "Chase", "name": "x", "account_number": "1234", "account_type": "saving"
    })
    assert r.status_code == 422


def test_create_with_notes(client):
    a = _make_account(client, notes="joint account with spouse")
    assert a["notes"] == "joint account with spouse"


def test_create_without_notes(client):
    a = _make_account(client)
    assert a["notes"] is None


def test_create_missing_bank_returns_422(client):
    r = client.post("/api/accounts", json={
        "name": "x", "account_number": "1234", "account_type": "checking"
    })
    assert r.status_code == 422


def test_create_missing_name_returns_422(client):
    r = client.post("/api/accounts", json={
        "bank": "Chase", "account_number": "1234", "account_type": "checking"
    })
    assert r.status_code == 422


def test_create_missing_account_number_returns_422(client):
    r = client.post("/api/accounts", json={
        "bank": "Chase", "name": "x", "account_type": "checking"
    })
    assert r.status_code == 422


def test_create_missing_account_type_returns_422(client):
    r = client.post("/api/accounts", json={
        "bank": "Chase", "name": "x", "account_number": "1234"
    })
    assert r.status_code == 422


# ── PATCH /api/accounts/{id} ──────────────────────────────────────────────────

def test_deactivate_account(client):
    a = _make_account(client)
    r = client.patch(f"/api/accounts/{a['id']}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_deactivate_nonexistent_returns_404(client):
    r = client.patch("/api/accounts/999", json={"is_active": False})
    assert r.status_code == 404


def test_reactivate_account(client):
    a = _make_account(client)
    client.patch(f"/api/accounts/{a['id']}", json={"is_active": False})
    r = client.patch(f"/api/accounts/{a['id']}", json={"is_active": True})
    assert r.json()["is_active"] is True


def test_patch_name(client):
    a = _make_account(client, name="Old Name")
    r = client.patch(f"/api/accounts/{a['id']}", json={"name": "New Name"})
    assert r.json()["name"] == "New Name"


def test_patch_does_not_affect_other_accounts(client):
    a1 = _make_account(client, name="A1")
    a2 = _make_account(client, name="A2")
    client.patch(f"/api/accounts/{a1['id']}", json={"is_active": False})
    a2_refreshed = client.get("/api/accounts").json()
    a2_data = next(a for a in a2_refreshed if a["id"] == a2["id"])
    assert a2_data["is_active"] is True


def test_list_accounts_ordered_by_created_desc(client):
    _make_account(client, name="First")
    _make_account(client, name="Second")
    accounts = client.get("/api/accounts").json()
    assert accounts[0]["name"] == "Second"


# ── Edit (full field PATCH) ───────────────────────────────────────────────────

def test_patch_bank(client):
    a = _make_account(client, bank="Old Bank")
    r = client.patch(f"/api/accounts/{a['id']}", json={"bank": "New Bank"})
    assert r.status_code == 200
    assert r.json()["bank"] == "New Bank"


def test_patch_account_type(client):
    a = _make_account(client, account_type="checking")
    r = client.patch(f"/api/accounts/{a['id']}", json={"account_type": "savings"})
    assert r.status_code == 200
    assert r.json()["account_type"] == "savings"


def test_patch_notes(client):
    a = _make_account(client)
    r = client.patch(f"/api/accounts/{a['id']}", json={"notes": "updated note"})
    assert r.status_code == 200
    assert r.json()["notes"] == "updated note"


def test_patch_multiple_fields(client):
    a = _make_account(client, bank="Old Bank", name="Old Name")
    r = client.patch(f"/api/accounts/{a['id']}", json={"bank": "New Bank", "name": "New Name"})
    assert r.status_code == 200
    data = r.json()
    assert data["bank"] == "New Bank"
    assert data["name"] == "New Name"


# ── Account linking ───────────────────────────────────────────────────────────

def test_link_account(client):
    source = _make_account(client, name="Source")
    target = _make_account(client, name="Target")
    r = client.patch(f"/api/accounts/{target['id']}", json={
        "source_account_id": source["id"],
        "source_amount": 500.0,
        "source_frequency": "monthly",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["source_account_id"] == source["id"]
    assert data["source_amount"] == 500.0
    assert data["source_frequency"] == "monthly"


def test_link_fields_returned_on_create(client):
    source = _make_account(client, name="Paycheck")
    a = _make_account(client,
        source_account_id=source["id"],
        source_amount=1000.0,
        source_frequency="weekly",
    )
    assert a["source_account_id"] == source["id"]
    assert a["source_amount"] == 1000.0
    assert a["source_frequency"] == "weekly"


def test_unlink_account(client):
    source = _make_account(client)
    target = _make_account(client, source_account_id=source["id"], source_amount=100.0, source_frequency="monthly")
    r = client.patch(f"/api/accounts/{target['id']}", json={
        "source_account_id": None,
        "source_amount": None,
        "source_frequency": None,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["source_account_id"] is None
    assert data["source_amount"] is None
    assert data["source_frequency"] is None


def test_self_link_returns_422(client):
    a = _make_account(client)
    r = client.patch(f"/api/accounts/{a['id']}", json={"source_account_id": a["id"]})
    assert r.status_code == 422


def test_link_nonexistent_source_returns_422(client):
    a = _make_account(client)
    r = client.patch(f"/api/accounts/{a['id']}", json={"source_account_id": 99999})
    assert r.status_code == 422


def test_invalid_source_frequency_returns_422(client):
    source = _make_account(client)
    target = _make_account(client)
    r = client.patch(f"/api/accounts/{target['id']}", json={
        "source_account_id": source["id"],
        "source_frequency": "biweekly",
    })
    assert r.status_code == 422


# ── Link validation ───────────────────────────────────────────────────────────

def test_deactivate_blocked_when_used_as_source(client):
    source = _make_account(client, name="Paycheck")
    _make_account(client, source_account_id=source["id"])
    r = client.patch(f"/api/accounts/{source['id']}", json={"is_active": False})
    assert r.status_code == 422
    assert "linked" in r.json()["detail"].lower()


def test_deactivate_allowed_after_unlink(client):
    source = _make_account(client)
    target = _make_account(client, source_account_id=source["id"])
    client.patch(f"/api/accounts/{target['id']}", json={"source_account_id": None})
    r = client.patch(f"/api/accounts/{source['id']}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_patch_link_to_inactive_source_returns_422(client):
    source = _make_account(client)
    target = _make_account(client)
    client.patch(f"/api/accounts/{source['id']}", json={"is_active": False})
    r = client.patch(f"/api/accounts/{target['id']}", json={"source_account_id": source["id"]})
    assert r.status_code == 422
    assert "not active" in r.json()["detail"].lower()


def test_create_with_inactive_source_returns_422(client):
    source = _make_account(client)
    client.patch(f"/api/accounts/{source['id']}", json={"is_active": False})
    r = client.post("/api/accounts", json={
        "bank": "New", "name": "New", "account_number": "9999",
        "account_type": "checking", "source_account_id": source["id"],
    })
    assert r.status_code == 422
    assert "not active" in r.json()["detail"].lower()


# ── Bank logos ────────────────────────────────────────────────────────────────

def test_get_bank_logos(client):
    r = client.get("/api/accounts/bank-logos")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
