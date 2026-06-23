import io


def _csv(rows: str) -> tuple:
    return ("test.csv", io.BytesIO(rows.encode()), "text/csv")


# ── /api/banks ────────────────────────────────────────────────────────────────

def test_banks_returns_list(client):
    r = client.get("/api/banks")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_banks_contains_configured_names(client):
    banks = client.get("/api/banks").json()
    assert "Chase" in banks
    assert "Ally" in banks


# ── /api/transactions/import — happy path ────────────────────────────────────

def test_import_minimal_csv(client):
    csv_data = "date,description,amount\n2024-01-15,Coffee,-4.50\n"
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 1
    assert body["errors"] == []


def test_import_sets_source_to_bank(client):
    csv_data = "date,description,amount\n2024-01-15,Coffee,-4.50\n"
    client.post(
        "/api/transactions/import",
        data={"bank": "Wells Fargo"},
        files={"file": _csv(csv_data)},
    )
    txs = client.get("/api/transactions").json()
    assert txs[0]["source"] == "Wells Fargo"


def test_import_with_optional_columns(client):
    csv_data = "date,description,amount,category,note\n2024-01-15,Coffee,-4.50,Food,morning\n"
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    assert r.json()["imported"] == 1
    tx = client.get("/api/transactions").json()[0]
    assert tx["category"] == "Food"
    assert tx["note"] == "morning"


def test_import_defaults_category_to_uncategorized(client):
    csv_data = "date,description,amount\n2024-01-15,Coffee,-4.50\n"
    client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    tx = client.get("/api/transactions").json()[0]
    assert tx["category"] == "Uncategorized"


def test_import_multiple_rows(client):
    csv_data = (
        "date,description,amount\n"
        "2024-01-15,Coffee,-4.50\n"
        "2024-01-16,Salary,2500.00\n"
        "2024-01-17,Lunch,-12.00\n"
    )
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    assert r.json()["imported"] == 3


def test_import_strips_bom(client):
    # b"\xef\xbb\xbf" is the actual UTF-8 BOM; "\xef\xbb\xbf" as a Python string
    # would encode to 6 bytes, not 3.
    csv_bytes = b"\xef\xbb\xbf" + b"date,description,amount\n2024-01-15,Coffee,-4.50\n"
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": ("test.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert r.json()["imported"] == 1
    assert r.json()["errors"] == []


# ── /api/transactions/import — error handling ─────────────────────────────────

def test_import_invalid_amount_skips_row(client):
    csv_data = "date,description,amount\n2024-01-15,Coffee,not-a-number\n"
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    body = r.json()
    assert body["imported"] == 0
    assert len(body["errors"]) == 1
    assert "Row 2" in body["errors"][0]


def test_import_missing_required_column_skips_row(client):
    csv_data = "date,amount\n2024-01-15,-4.50\n"
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    body = r.json()
    assert body["imported"] == 0
    assert len(body["errors"]) == 1


def test_import_invalid_date_skips_row(client):
    csv_data = "date,description,amount\nnot-a-date,Coffee,-4.50\n"
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    body = r.json()
    assert body["imported"] == 0
    assert len(body["errors"]) == 1


def test_import_partial_success(client):
    csv_data = (
        "date,description,amount\n"
        "2024-01-15,Coffee,-4.50\n"
        "2024-01-16,Bad,oops\n"
        "2024-01-17,Lunch,-12.00\n"
    )
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    body = r.json()
    assert body["imported"] == 2
    assert len(body["errors"]) == 1
    assert "Row 3" in body["errors"][0]


def test_import_empty_csv(client):
    csv_data = "date,description,amount\n"
    r = client.post(
        "/api/transactions/import",
        data={"bank": "Chase"},
        files={"file": _csv(csv_data)},
    )
    body = r.json()
    assert body["imported"] == 0
    assert body["errors"] == []
