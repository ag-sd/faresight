import pytest


VALID_RULE = {
    "description": "TRADER JOE'S #542",
    "category": "Groceries",
}


# ── CRUD ──────────────────────────────────────────────────────────────────────

def test_create_rule(client):
    r = client.post("/api/rules", json=VALID_RULE)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] is not None
    assert body["description"] == VALID_RULE["description"]
    assert body["category"] == VALID_RULE["category"]
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


# ── Stage 3: rule pre-classification ─────────────────────────────────────────

def _make_account(client, default_importer="Capital One Credit Card"):
    r = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": "My Card",
        "account_number": "1234",
        "account_type": "credit_card",
        "default_importer": default_importer,
    })
    assert r.status_code == 201
    return r.json()["id"]


def _import_csv(client, account_id, csv_content):
    return client.post(
        "/api/transactions/import-bulk",
        data={"account_id": account_id},
        files={"files": ("test.csv", csv_content.encode(), "text/csv")},
    )


SAMPLE_CSV = """Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2026-06-01,2026-06-02,1234,TRADER JOE'S #542,Merchandise,48.30,
2026-06-03,2026-06-04,1234,NETFLIX.COM,Entertainment,,15.49
"""


def test_file_import_saves_importer(client):
    account_id = _make_account(client)
    r = _import_csv(client, account_id, SAMPLE_CSV)
    assert r.status_code == 200

    fi = client.get("/api/file-imports").json()["data"]
    assert len(fi) == 1
    assert fi[0]["importer"] == "Capital One Credit Card"


def test_rule_pre_classifies_matching_transaction(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
    })

    _import_csv(client, account_id, SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    assert trader_joes["model_category"] == "Groceries"
    assert trader_joes["model_confidence"] == 10


def test_unmatched_transaction_stays_pending(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
    })

    _import_csv(client, account_id, SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    netflix = next(t for t in txs if "NETFLIX" in t["description"])
    # model_confidence -1 is masked to null in the API response
    assert netflix["model_confidence"] is None


def test_rule_applies_across_all_accounts(client):
    """A rule fires for any account — not scoped to a single importer."""
    a1 = client.post("/api/accounts", json={
        "bank": "Capital One", "name": "Card A", "account_number": "0001",
        "account_type": "credit_card", "default_importer": "Capital One Credit Card",
    }).json()["id"]
    a2 = client.post("/api/accounts", json={
        "bank": "Capital One", "name": "Card B", "account_number": "0002",
        "account_type": "credit_card", "default_importer": "Capital One Credit Card",
    }).json()["id"]
    client.post("/api/rules", json={"description": "TRADER JOE'S #542", "category": "Groceries"})

    _import_csv(client, a1, SAMPLE_CSV)
    _import_csv(client, a2, SAMPLE_CSV)

    txs = client.get("/api/transactions?limit=100").json()["data"]
    trader_joes = [t for t in txs if "TRADER JOE" in t["description"]]
    assert len(trader_joes) == 2
    assert all(t["model_category"] == "Groceries" for t in trader_joes)


# ── Stage 4: "Run Rule Now" ───────────────────────────────────────────────────

def test_apply_rule_updates_matching_transactions(client):
    account_id = _make_account(client)
    _import_csv(client, account_id, SAMPLE_CSV)

    rule = client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
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
    _import_csv(client, account_id, SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])

    # What the edit modal really sends: a pinned display-category edit.
    client.patch(f"/api/transactions/{trader_joes['id']}", json={
        "model_category": "Shopping",
        "model_confidence": 10,
        "user_modified_category": True,
    })

    rule = client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
    }).json()

    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.json()["updated"] == 0

    refreshed = client.get(f"/api/transactions/{trader_joes['id']}").json()
    assert refreshed["model_category"] == "Shopping"


def test_apply_rule_no_transactions(client):
    rule = client.post("/api/rules", json=VALID_RULE).json()
    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.status_code == 200
    assert r.json()["updated"] == 0


def test_apply_rule_not_found(client):
    r = client.post("/api/rules/999/apply")
    assert r.status_code == 404


def test_apply_rule_affects_all_accounts(client):
    """Run Now applies to matching transactions across all accounts."""
    a1 = client.post("/api/accounts", json={
        "bank": "Capital One", "name": "Card A", "account_number": "0001",
        "account_type": "credit_card", "default_importer": "Capital One Credit Card",
    }).json()["id"]
    a2 = client.post("/api/accounts", json={
        "bank": "Capital One", "name": "Card B", "account_number": "0002",
        "account_type": "credit_card", "default_importer": "Capital One Credit Card",
    }).json()["id"]
    _import_csv(client, a1, SAMPLE_CSV)
    _import_csv(client, a2, SAMPLE_CSV)

    rule = client.post("/api/rules", json={
        "description": "TRADER JOE'S #542",
        "category": "Groceries",
    }).json()

    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.json()["updated"] == 2


# ── Regex matching ────────────────────────────────────────────────────────────
# Rule descriptions are regex patterns: case-insensitive, matched anywhere in
# the transaction description (re.search). Plain text behaves as "contains".

def test_create_rule_invalid_regex(client):
    r = client.post("/api/rules", json={**VALID_RULE, "description": "(["})
    assert r.status_code == 422
    assert "Invalid regular expression" in r.json()["detail"]


def test_regex_rule_matches_substring_case_insensitive(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": "trader joe",
        "category": "Groceries",
    })

    _import_csv(client, account_id, SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    assert trader_joes["model_category"] == "Groceries"
    assert trader_joes["model_confidence"] == 10


def test_regex_rule_with_metacharacters(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": r"TRADER JOE'S #\d+",
        "category": "Groceries",
    })
    client.post("/api/rules", json={
        "description": "NETFLIX|HULU",
        "category": "Entertainment & Subscriptions",
    })

    _import_csv(client, account_id, SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    netflix = next(t for t in txs if "NETFLIX" in t["description"])
    assert trader_joes["model_category"] == "Groceries"
    assert netflix["model_category"] == "Entertainment & Subscriptions"


def test_oldest_rule_wins_when_multiple_match(client):
    account_id = _make_account(client)
    client.post("/api/rules", json={
        "description": "trader",
        "category": "Shopping",
    })
    client.post("/api/rules", json={
        "description": "joe",
        "category": "Dining & Takeout",
    })

    _import_csv(client, account_id, SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    assert trader_joes["model_category"] == "Shopping"  # first-created rule wins


def test_uncompilable_legacy_pattern_skipped_at_import(client):
    """A bad pattern inserted before create-time validation must never break imports."""
    from tests.conftest import TestingSession
    from app.models import Rule

    db = TestingSession()
    try:
        db.add(Rule(description="([", category="Groceries"))
        db.add(Rule(description="netflix", category="Entertainment & Subscriptions"))
        db.commit()
    finally:
        db.close()

    account_id = _make_account(client)
    r = _import_csv(client, account_id, SAMPLE_CSV)
    assert r.status_code == 200

    txs = client.get("/api/transactions").json()["data"]
    netflix = next(t for t in txs if "NETFLIX" in t["description"])
    assert netflix["model_category"] == "Entertainment & Subscriptions"  # good rule still fired
    trader_joes = next(t for t in txs if "TRADER JOE" in t["description"])
    assert trader_joes["model_confidence"] is None  # bad rule silently skipped


def test_apply_regex_rule_updates_matching_transactions(client):
    account_id = _make_account(client)
    _import_csv(client, account_id, SAMPLE_CSV)

    rule = client.post("/api/rules", json={
        "description": "netflix",  # lowercase substring of NETFLIX.COM
        "category": "Entertainment & Subscriptions",
    }).json()

    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    txs = client.get("/api/transactions").json()["data"]
    netflix = next(t for t in txs if "NETFLIX" in t["description"])
    assert netflix["model_category"] == "Entertainment & Subscriptions"
    assert netflix["model_confidence"] == 10


def test_apply_regex_rule_skips_user_modified(client):
    account_id = _make_account(client)
    _import_csv(client, account_id, SAMPLE_CSV)

    txs = client.get("/api/transactions").json()["data"]
    netflix = next(t for t in txs if "NETFLIX" in t["description"])
    client.patch(f"/api/transactions/{netflix['id']}", json={
        "model_category": "Shopping",
        "model_confidence": 10,
        "user_modified_category": True,
    })

    rule = client.post("/api/rules", json={
        "description": "netflix",
        "category": "Entertainment & Subscriptions",
    }).json()

    assert client.post(f"/api/rules/{rule['id']}/apply").json()["updated"] == 0
    refreshed = client.get(f"/api/transactions/{netflix['id']}").json()
    assert refreshed["model_category"] == "Shopping"


# ── PATCH /api/rules/{id} ─────────────────────────────────────────────────────

def test_update_rule_partial_fields(client):
    rule = client.post("/api/rules", json=VALID_RULE).json()

    r = client.patch(f"/api/rules/{rule['id']}", json={"description": "trader joe"})
    assert r.status_code == 200
    body = r.json()
    assert body["description"] == "trader joe"
    assert body["category"] == VALID_RULE["category"]      # untouched

    r = client.patch(f"/api/rules/{rule['id']}", json={"category": "Shopping"})
    assert r.status_code == 200
    assert r.json()["category"] == "Shopping"

    # Persisted, not just echoed.
    listed = client.get("/api/rules").json()
    assert listed[0]["description"] == "trader joe"
    assert listed[0]["category"] == "Shopping"


def test_update_rule_not_found(client):
    r = client.patch("/api/rules/999", json={"category": "Shopping"})
    assert r.status_code == 404


def test_update_rule_unknown_category(client):
    rule = client.post("/api/rules", json=VALID_RULE).json()
    r = client.patch(f"/api/rules/{rule['id']}", json={"category": "NotACategory"})
    assert r.status_code == 422


def test_update_rule_invalid_regex(client):
    rule = client.post("/api/rules", json=VALID_RULE).json()
    r = client.patch(f"/api/rules/{rule['id']}", json={"description": "(["})
    assert r.status_code == 422
    assert "Invalid regular expression" in r.json()["detail"]


def test_update_rule_duplicate_conflict(client):
    client.post("/api/rules", json=VALID_RULE)
    other = client.post("/api/rules", json={**VALID_RULE, "description": "NETFLIX"}).json()

    # Patching the second rule into an exact copy of the first → 409.
    r = client.patch(f"/api/rules/{other['id']}", json={"description": VALID_RULE["description"]})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_apply_rule_chunks_large_id_lists(client, monkeypatch):
    """The IN-clause update is chunked; matches spanning several chunks are all
    updated and counted exactly once."""
    import app.routers.rules as rules_mod
    monkeypatch.setattr(rules_mod, "_APPLY_CHUNK_SIZE", 2)

    header = "Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
    rows = "".join(
        f"2026-06-{day:02d},2026-06-{day:02d},1234,COFFEE SHOP #{day},Dining,4.50,\n"
        for day in range(1, 6)  # 5 matching rows across 3 chunks of 2
    )
    account_id = _make_account(client)
    _import_csv(client, account_id, header + rows)

    rule = client.post("/api/rules", json={
        "description": "coffee shop",
        "category": "Dining & Takeout",
    }).json()

    r = client.post(f"/api/rules/{rule['id']}/apply")
    assert r.status_code == 200
    assert r.json()["updated"] == 5

    txs = client.get("/api/transactions?limit=100").json()["data"]
    coffee = [t for t in txs if "COFFEE SHOP" in t["description"]]
    assert len(coffee) == 5
    assert all(t["model_category"] == "Dining & Takeout" for t in coffee)


def test_updated_rule_applies_with_new_pattern(client):
    """Editing a rule's pattern changes what Run Now matches."""
    account_id = _make_account(client)
    _import_csv(client, account_id, SAMPLE_CSV)

    rule = client.post("/api/rules", json={
        "description": "WILL NOT MATCH ANYTHING",
        "category": "Entertainment & Subscriptions",
    }).json()
    assert client.post(f"/api/rules/{rule['id']}/apply").json()["updated"] == 0

    client.patch(f"/api/rules/{rule['id']}", json={"description": "netflix"})
    assert client.post(f"/api/rules/{rule['id']}/apply").json()["updated"] == 1
