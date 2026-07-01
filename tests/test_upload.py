import pathlib

SAMPLE_CSV = pathlib.Path(__file__).parent / "capitalone_sample.csv"
CAPONE_IMPORTER = "Capital One Credit Card"



def _make_account(client):
    r = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": "Venture",
        "account_number": "1543",
        "account_type": "credit_card",
    })
    assert r.status_code == 201, r.text
    return r.json()


# ── GET /api/importers ────────────────────────────────────────────────────────

def test_list_importers_returns_200(client):
    r = client.get("/api/importers")
    assert r.status_code == 200


def test_list_importers_is_list(client):
    r = client.get("/api/importers")
    assert isinstance(r.json(), list)


def test_list_importers_contains_capitalone(client):
    assert CAPONE_IMPORTER in client.get("/api/importers").json()


# ── POST /api/transactions/import-bulk ───────────────────────────────────────

def test_import_bulk_single_file(client):
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["filename"] == "sample.csv"
    assert results[0]["imported"] == 13
    assert results[0]["errors"] == []


def test_import_bulk_transactions_saved_to_db(client):
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    body = client.get("/api/transactions").json()
    assert body["total"] == 13


def test_import_bulk_multiple_files(client):
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[
            ("files", ("jan.csv", csv_bytes, "text/csv")),
            ("files", ("feb.csv", csv_bytes, "text/csv")),
        ],
    )
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 2
    assert results[0]["filename"] == "jan.csv"
    assert results[1]["filename"] == "feb.csv"
    assert results[0]["imported"] == 13
    assert results[1]["imported"] == 13  # no dedup — all rows imported again



def test_import_bulk_unknown_account_returns_404(client):
    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": 9999, "importer": CAPONE_IMPORTER},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    assert r.status_code == 404


def test_import_bulk_unknown_importer_returns_400(client):
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": "Nonexistent Bank"},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    assert r.status_code == 400


def test_import_bulk_partial_errors_reported_per_file(client):
    acct = _make_account(client)
    bad_csv = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-01,2026-01-02,1234,Good Row,Food,10.00,\n"
        b"bad-date,2026-01-02,1234,Bad Row,Food,10.00,\n"
    )
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("bad.csv", bad_csv, "text/csv"))],
    )
    assert r.status_code == 200
    result = r.json()[0]
    assert result["imported"] == 1
    assert len(result["errors"]) == 1


def test_import_bulk_empty_csv_zero_imported(client):
    acct = _make_account(client)
    empty_csv = b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("empty.csv", empty_csv, "text/csv"))],
    )
    assert r.status_code == 200
    result = r.json()[0]
    assert result["imported"] == 0
    assert result["errors"] == []


def test_import_bulk_errors_do_not_block_other_files(client):
    acct = _make_account(client)
    bad_csv = b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\nbad-date,x,x,x,x,1,\n"
    good_csv = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[
            ("files", ("bad.csv",  bad_csv,  "text/csv")),
            ("files", ("good.csv", good_csv, "text/csv")),
        ],
    )
    assert r.status_code == 200
    results = r.json()
    bad  = next(x for x in results if x["filename"] == "bad.csv")
    good = next(x for x in results if x["filename"] == "good.csv")
    assert bad["imported"] == 0
    assert len(bad["errors"]) == 1
    assert good["imported"] == 13
    assert good["errors"] == []


# ── Categorization wiring ─────────────────────────────────────────────────────

def test_import_bulk_marks_transactions_pending(client):
    """Regular rows are pending; payment rows are pre-classified as Transfers & Fees."""
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    txs = client.get("/api/transactions?limit=100").json()["data"]
    regular = [tx for tx in txs if tx["model_category"] != "Transfers & Fees"]
    payments = [tx for tx in txs if tx["model_category"] == "Transfers & Fees"]
    assert len(txs) == 13
    assert all(tx["model_confidence"] is None for tx in regular)
    assert len(payments) == 2
    assert all(tx["model_confidence"] == 10 for tx in payments)


# ── GET /api/categorizer/status ───────────────────────────────────────────────

def test_categorizer_status_empty(client):
    r = client.get("/api/categorizer/status")
    assert r.status_code == 200
    assert r.json()["pending"] == 0
    assert r.json()["categorized"] == 0


def test_categorizer_status_after_import(client):
    """Regular rows are pending; payment rows (pre-classified) count as categorized."""
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    r = client.get("/api/categorizer/status").json()
    assert r["pending"] == 11    # 13 rows minus 2 payment rows
    assert r["categorized"] == 2  # the 2 CAPITAL ONE MOBILE PYMT rows


def test_categorizer_status_after_categorization(client):
    """Rows whose model_confidence is updated move from pending to categorized."""
    from app.models import Transaction as Tx
    from tests.conftest import TestingSession

    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    # Simulate the background worker writing back results for 5 rows (ids 1-5).
    # id=2 is a payment row already at confidence=10; updating it to 7 keeps it categorized.
    db = TestingSession()
    db.query(Tx).filter(Tx.id <= 5).update(
        {"model_confidence": 7, "model_category": "Shopping"}
    )
    db.commit()
    db.close()

    r = client.get("/api/categorizer/status").json()
    # ids 6-9 (4) + ids 11-13 (3) = 7 pending; ids 1-5 (5) + id 10 (payment) = 6 categorized
    assert r["pending"] == 7
    assert r["categorized"] == 6


# ── FileImport log ────────────────────────────────────────────────────────────

def test_import_creates_file_import_record(client):
    from app.models import FileImport
    from tests.conftest import TestingSession

    acct = _make_account(client)
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("sample.csv", SAMPLE_CSV.read_bytes(), "text/csv"))],
    )
    db = TestingSession()
    records = db.query(FileImport).all()
    db.close()
    assert len(records) == 1
    r = records[0]
    assert r.filename == "sample.csv"
    assert r.rows_seen == 13
    assert r.rows_persisted == 13
    assert r.loaded_at is not None


def test_reimport_creates_second_record(client):
    from app.models import FileImport
    from tests.conftest import TestingSession

    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    for _ in range(2):
        client.post(
            "/api/transactions/import-bulk",
            data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
            files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
        )
    db = TestingSession()
    records = db.query(FileImport).order_by(FileImport.id).all()
    db.close()
    assert len(records) == 2
    assert records[0].rows_persisted == 13
    assert records[1].rows_seen == 13
    assert records[1].rows_persisted == 13


def test_multi_file_import_creates_one_record_per_file(client):
    from app.models import FileImport
    from tests.conftest import TestingSession

    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[
            ("files", ("jan.csv", csv_bytes, "text/csv")),
            ("files", ("feb.csv", csv_bytes, "text/csv")),
        ],
    )
    db = TestingSession()
    records = db.query(FileImport).order_by(FileImport.id).all()
    db.close()
    assert len(records) == 2
    assert records[0].filename == "jan.csv"
    assert records[1].filename == "feb.csv"


def test_partial_error_rows_seen_includes_bad_rows(client):
    from app.models import FileImport
    from tests.conftest import TestingSession

    acct = _make_account(client)
    csv = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-01,2026-01-02,1234,Good Row,Food,10.00,\n"
        b"bad-date,2026-01-02,1234,Bad Row,Food,10.00,\n"
    )
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("mixed.csv", csv, "text/csv"))],
    )
    db = TestingSession()
    record = db.query(FileImport).one()
    db.close()
    assert record.rows_seen == 2
    assert record.rows_persisted == 1


def test_get_file_imports_endpoint(client):
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("first.csv", csv_bytes, "text/csv"))],
    )
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
        files=[("files", ("second.csv", csv_bytes, "text/csv"))],
    )
    r = client.get("/api/file-imports")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    data = body["data"]
    assert len(data) == 2
    # newest first
    assert data[0]["filename"] == "second.csv"
    assert data[1]["filename"] == "first.csv"
    assert "rows_seen" in data[0]
    assert "rows_persisted" in data[0]
    assert "loaded_at" in data[0]


def test_file_imports_pagination(client):
    acct = _make_account(client)
    one_row_csv = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-01,2026-01-02,1234,Coffee,Food,5.00,\n"
    )
    for i in range(26):
        client.post(
            "/api/transactions/import-bulk",
            data={"account_id": acct["id"], "importer": CAPONE_IMPORTER},
            files=[("files", (f"batch_{i:02d}.csv", one_row_csv, "text/csv"))],
        )
    page1 = client.get("/api/file-imports?page=1&limit=25").json()
    assert page1["total"] == 26
    assert len(page1["data"]) == 25
    page2 = client.get("/api/file-imports?page=2&limit=25").json()
    assert len(page2["data"]) == 1


def test_categorizer_status_excludes_null_confidence(client):
    """Legacy rows with model_confidence IS NULL are not counted in either bucket."""
    from sqlalchemy import text
    from tests.conftest import TestingSession

    # Use raw SQL to bypass the ORM default so the row lands with NULL confidence.
    from app.models import FileImport
    db = TestingSession()
    fi = FileImport(filename="test.csv", rows_seen=1, rows_persisted=0)
    db.add(fi)
    db.flush()
    db.execute(text(
        "INSERT INTO transactions (date, description, amount, category, model_confidence, user_modified_category, file_id)"
        f" VALUES ('2026-01-01', 'legacy row', -5.0, 'Food', NULL, 0, {fi.id})"
    ))
    db.commit()
    db.close()

    r = client.get("/api/categorizer/status").json()
    assert r["pending"] == 0
    assert r["categorized"] == 0
