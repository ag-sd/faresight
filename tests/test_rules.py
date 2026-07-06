import pytest


VALID_RULE = {
    "description": "TRADER JOE'S #542",
    "category": "Groceries",
    "importer": "Capital One Credit Card",
}


# ── CRUD ──────────────────────────────────────────────────────────────────────

def test_create_rule(client):
    r = client.post("/api/rules", json=VALID_RULE)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] is not None
    assert body["description"] == VALID_RULE["description"]
    assert body["category"] == VALID_RULE["category"]
    assert body["importer"] == VALID_RULE["importer"]
    assert "created_at" in body


def test_list_rules_empty(client):
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert r.json() == []


def test_list_rules(client):
    client.post("/api/rules", json=VALID_RULE)
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_list_rules_filter_by_importer(client):
    client.post("/api/rules", json=VALID_RULE)
    client.post("/api/rules", json={**VALID_RULE, "importer": "Capital One Checking/Savings"})

    r = client.get("/api/rules?importer=Capital One Credit Card")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["importer"] == "Capital One Credit Card"

    r = client.get("/api/rules?importer=Capital One Checking/Savings")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_delete_rule(client):
    created = client.post("/api/rules", json=VALID_RULE).json()
    r = client.delete(f"/api/rules/{created['id']}")
    assert r.status_code == 204

    r = client.get("/api/rules")
    assert r.json() == []


def test_delete_rule_not_found(client):
    r = client.delete("/api/rules/999")
    assert r.status_code == 404


# ── Validation ────────────────────────────────────────────────────────────────

def test_create_rule_unknown_category(client):
    r = client.post("/api/rules", json={**VALID_RULE, "category": "NotACategory"})
    assert r.status_code == 422


def test_create_rule_unknown_importer(client):
    r = client.post("/api/rules", json={**VALID_RULE, "importer": "Ghost Bank"})
    assert r.status_code == 422


def test_create_rule_duplicate(client):
    client.post("/api/rules", json=VALID_RULE)
    r = client.post("/api/rules", json=VALID_RULE)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_create_rule_all_allowed_categories(client):
    categories = [c["name"] for c in client.get("/api/categories").json()]
    for category in categories:
        r = client.post("/api/rules", json={**VALID_RULE, "category": category})
        assert r.status_code == 201, f"Failed for category {category!r}: {r.text}"


# ── Stage 3: importer saved in file_imports + rule pre-classification ─────────

def _make_account(client):
    r = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": "My Card",
        "account_number": "1234",
        "account_type": "credit_card",
    })
    assert r.status_code == 201
    return r.json()["id"]


def _import_csv(client, account_id, importer, csv_content):
    return client.post(
        "/api/transactions/import-bulk",
        data={"account_id": account_id, "importer": importer},
        files={"files": ("test.csv", csv_content.encode(), "text/csv")},
    )


SAMPLE_CSV = """Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2026-06-01,2026-06-02,1234,TRADER JOE'S #542,Merchandise,48.30,
2026-06-03,2026-06-04,1234,NETFLIX.COM,Entertainment,,15.49
"""


def test_file_import_saves_importer(client):
    account_id = _make_account(client)
    r = _import_csv(client, account_id, "Capital One Credit Card", SAMPLE_CSV)
    assert r.status_code == 200

    fi = client.get("/api/file-imports").json()["data"]
    assert len(fi) == 1
    assert fi[0]["importer"] == "Capital One Credit Card"


def test_rule_pre_classifies_matching_transaction(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
        "importer": "Capital One Credit Card",
    })

    _import_csv(client, account_id, "Capital One Credit Card", SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    assert trader_joes["model_category"] == "Groceries"
    assert trader_joes["model_confidence"] == 10


def test_unmatched_transaction_stays_pending(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
        "importer": "Capital One Credit Card",
    })

    _import_csv(client, account_id, "Capital One Credit Card", SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    netflix = next(t for t in txs if "NETFLIX" in t["description"])
    # model_confidence -1 is masked to null in the API response
    assert netflix["model_confidence"] is None


def test_rule_does_not_apply_to_wrong_importer(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
        "importer": "Capital One Checking/Savings",  # different importer
    })

    _import_csv(client, account_id, "Capital One Credit Card", SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    assert trader_joes["model_confidence"] is None  # still pending, rule didn't fire


# ── Stage 4: "Run Rule Now" ───────────────────────────────────────────────────

def test_apply_rule_updates_matching_transactions(client):
    account_id = _make_account(client)
    _import_csv(client, account_id, "Capital One Credit Card", SAMPLE_CSV)

    rule = client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
        "importer": "Capital One Credit Card",
    }).json()

    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    assert trader_joes["model_category"] == "Groceries"
    assert trader_joes["model_confidence"] == 10


def test_apply_rule_skips_user_modified_transactions(client):
    account_id = _make_account(client)
    _import_csv(client, account_id, "Capital One Credit Card", SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])

    client.patch(f"/api/transactions/{trader_joes['id']}", json={
        "category": "Shopping",
        "user_modified_category": True,
    })

    rule = client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
        "importer": "Capital One Credit Card",
    }).json()

    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.json()["updated"] == 0

    refreshed = client.get(f"/api/transactions/{trader_joes['id']}").json()
    assert refreshed["category"] == "Shopping"


def test_apply_rule_no_matching_file_imports(client):
    rule = client.post("/api/rules", json=VALID_RULE).json()
    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.status_code == 200
    assert r.json()["updated"] == 0


def test_apply_rule_not_found(client):
    r = client.post("/api/rules/999/apply")
    assert r.status_code == 404


def test_apply_rule_does_not_touch_different_importer_imports(client):
    account_id = _make_account(client)
    _import_csv(client, account_id, "Capital One Credit Card", SAMPLE_CSV)

    rule = client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
        "importer": "Capital One Checking/Savings",  # different importer
    }).json()

    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.json()["updated"] == 0
