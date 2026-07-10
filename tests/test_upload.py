import pathlib

SAMPLE_CSV = pathlib.Path(__file__).parent / "capitalone_sample.csv"
CAPONE_IMPORTER = "Capital One Credit Card"



def _make_account(client):
    r = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": "Venture",
        "account_number": "1543",
        "account_type": "credit_card",
        "default_importer": CAPONE_IMPORTER,
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
        data={"account_id": acct["id"]},
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
        data={"account_id": acct["id"]},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    body = client.get("/api/transactions").json()
    assert body["total"] == 13


def test_import_bulk_multiple_files(client):
    """Identical bytes twice in one request: the second file is an exact
    duplicate and is short-circuited by the re-import guard."""
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
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
    assert results[1]["imported"] == 0
    assert results[1]["duplicate_file"] is True



def test_import_bulk_unknown_account_returns_404(client):
    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": 9999},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    assert r.status_code == 404


def test_import_bulk_account_with_invalid_importer_returns_400(client):
    # The importer is now derived from the account. An account whose stored
    # importer is not in the registry (e.g. a legacy row) cannot be imported.
    from app.models import Account
    from tests.conftest import TestingSession
    db = TestingSession()
    acct = Account(
        bank="Capital One", name="Venture", account_number="1543",
        account_type="credit_card", default_importer="Nonexistent Bank",
    )
    db.add(acct)
    db.commit()
    acct_id = acct.id
    db.close()

    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct_id},
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
        data={"account_id": acct["id"]},
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
        data={"account_id": acct["id"]},
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
        data={"account_id": acct["id"]},
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
    """All imported rows start pending — classification is handled by the rules system."""
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    txs = client.get("/api/transactions?limit=100").json()["data"]
    assert len(txs) == 13
    assert all(tx["model_confidence"] is None for tx in txs)
    assert all(tx["model_category"] is None for tx in txs)


# ── GET /api/categorizer/status ───────────────────────────────────────────────

def test_categorizer_status_empty(client):
    r = client.get("/api/categorizer/status")
    assert r.status_code == 200
    assert r.json()["pending"] == 0
    assert r.json()["categorized"] == 0


def test_categorizer_status_after_import(client):
    """All imported rows start pending — payment classification is handled by rules, not the importer."""
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
    )
    r = client.get("/api/categorizer/status").json()
    assert r["pending"] == 13
    assert r["categorized"] == 0


def test_categorizer_status_after_categorization(client):
    """Rows whose model_confidence is updated move from pending to categorized."""
    from app.models import Transaction as Tx
    from tests.conftest import TestingSession

    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
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
    # ids 1-5 updated to confidence=7 → categorized; ids 6-13 still pending
    assert r["pending"] == 8
    assert r["categorized"] == 5


# ── FileImport log ────────────────────────────────────────────────────────────

def test_import_creates_file_import_record(client):
    from app.models import FileImport
    from tests.conftest import TestingSession

    acct = _make_account(client)
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
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


def test_reimport_short_circuits_without_second_record(client):
    """Re-uploading identical bytes is caught by the file-hash guard: no new
    FileImport row, no new transactions."""
    from app.models import FileImport
    from tests.conftest import TestingSession

    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    for _ in range(2):
        r = client.post(
            "/api/transactions/import-bulk",
            data={"account_id": acct["id"]},
            files=[("files", ("sample.csv", csv_bytes, "text/csv"))],
        )
    assert r.json()[0]["duplicate_file"] is True
    db = TestingSession()
    records = db.query(FileImport).order_by(FileImport.id).all()
    db.close()
    assert len(records) == 1
    assert records[0].rows_persisted == 13
    assert client.get("/api/transactions").json()["total"] == 13


def test_multi_file_import_creates_one_record_per_file(client):
    from app.models import FileImport
    from tests.conftest import TestingSession

    acct = _make_account(client)
    jan_csv = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-05,2026-01-06,1234,Coffee,Dining,5.00,\n"
    )
    feb_csv = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-02-05,2026-02-06,1234,Groceries,Food,42.00,\n"
    )
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[
            ("files", ("jan.csv", jan_csv, "text/csv")),
            ("files", ("feb.csv", feb_csv, "text/csv")),
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
        data={"account_id": acct["id"]},
        files=[("files", ("mixed.csv", csv, "text/csv"))],
    )
    db = TestingSession()
    record = db.query(FileImport).one()
    db.close()
    assert record.rows_seen == 2
    assert record.rows_persisted == 1


def test_get_file_imports_endpoint(client):
    acct = _make_account(client)
    first_csv = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-05,2026-01-06,1234,Coffee,Dining,5.00,\n"
    )
    second_csv = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-02-05,2026-02-06,1234,Groceries,Food,42.00,\n"
    )
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", ("first.csv", first_csv, "text/csv"))],
    )
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", ("second.csv", second_csv, "text/csv"))],
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
    for i in range(26):
        # Unique content per file so the exact-duplicate guard doesn't skip them.
        one_row_csv = (
            b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
            + f"2026-01-01,2026-01-02,1234,Coffee {i},Food,5.00,\n".encode()
        )
        client.post(
            "/api/transactions/import-bulk",
            data={"account_id": acct["id"]},
            files=[("files", (f"batch_{i:02d}.csv", one_row_csv, "text/csv"))],
        )
    page1 = client.get("/api/file-imports?page=1&limit=25").json()
    assert page1["total"] == 26
    assert len(page1["data"]) == 25
    page2 = client.get("/api/file-imports?page=2&limit=25").json()
    assert len(page2["data"]) == 1


def test_import_bulk_filename_with_special_chars_preserved(client):
    """Filenames containing HTML special characters are echoed verbatim by the API.

    The frontend is responsible for safe rendering (text nodes, not innerHTML);
    this test confirms the pipeline never mangles or strips the characters before
    they reach the frontend.
    """
    acct = _make_account(client)
    # Double-quote is percent-encoded by httpx's multipart transport, so we use
    # angle brackets (the critical XSS injection chars) which pass through verbatim.
    special_name = 'weird <img src=x onerror=alert(1)>.csv'
    csv_bytes = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", (special_name, csv_bytes, "text/csv"))],
    )
    assert r.status_code == 200
    result = r.json()[0]
    assert result["filename"] == special_name


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
        "INSERT INTO transactions (date, description, amount, bank_category, model_confidence, user_modified_category, file_id)"
        f" VALUES ('2026-01-01', 'legacy row', -5.0, 'Food', NULL, 0, {fi.id})"
    ))
    db.commit()
    db.close()

    r = client.get("/api/categorizer/status").json()
    assert r["pending"] == 0
    assert r["categorized"] == 0


# ── account.current_balance from newest row (date-order regression) ──────────

SAVINGS_IMPORTER = "Capital One Checking/Savings"


def _make_savings_account(client):
    r = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": "360 Savings",
        "account_number": "1543",
        "account_type": "savings",
        "default_importer": SAVINGS_IMPORTER,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_import_savings_ascending_order_sets_newest_balance(client):
    """Ascending-date CSV: current_balance must reflect the newest (last) row, not the first."""
    acct = _make_savings_account(client)
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Old Withdrawal,04/14/26,Debit,65000,10000.00\n"
        b"1543,Middle Credit,05/31/26,Credit,500,10500.00\n"
        b"1543,Newest Deposit,06/23/26,Credit,1000,11500.00\n"
    )
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", ("savings.csv", csv_bytes, "text/csv"))],
    )
    assert r.status_code == 200
    assert r.json()[0]["imported"] == 3
    acc = next(a for a in client.get("/api/accounts").json() if a["id"] == acct["id"])
    assert acc["current_balance"] == 11500.00


SAVINGS_HEADER = (
    b"Account Number,Transaction Description,Transaction Date,Transaction Type,"
    b"Transaction Amount,Balance\n"
)


def _upload_savings(client, acct, filename, rows: bytes):
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", (filename, SAVINGS_HEADER + rows, "text/csv"))],
    )
    assert r.status_code == 200, r.text
    return r.json()[0]


def _balance_of(client, acct):
    return next(a for a in client.get("/api/accounts").json() if a["id"] == acct["id"])["current_balance"]


def test_older_statement_does_not_regress_balance(client):
    """Out-of-order backfill: an older statement's snapshot must not overwrite
    the balance set by a newer one (BalanceSnapshot contract: newest wins)."""
    acct = _make_savings_account(client)
    _upload_savings(client, acct, "june.csv",
                    b"1543,June Deposit,06/23/26,Credit,1000,11500.00\n")
    assert _balance_of(client, acct) == 11500.00

    result = _upload_savings(client, acct, "april.csv",
                             b"1543,April Withdrawal,04/14/26,Debit,500,9000.00\n")
    assert result["imported"] == 1  # backfilled rows still import
    assert _balance_of(client, acct) == 11500.00  # balance did not regress


def test_newer_statement_still_updates_balance(client):
    acct = _make_savings_account(client)
    _upload_savings(client, acct, "april.csv",
                    b"1543,April Withdrawal,04/14/26,Debit,500,9000.00\n")
    assert _balance_of(client, acct) == 9000.00

    _upload_savings(client, acct, "june.csv",
                    b"1543,June Deposit,06/23/26,Credit,1000,11500.00\n")
    assert _balance_of(client, acct) == 11500.00


def test_equal_as_of_snapshot_reapplies(client):
    """Tie behavior: a snapshot dated the same as the latest recorded one wins,
    so a corrected export for the same date re-applies."""
    acct = _make_savings_account(client)
    _upload_savings(client, acct, "first.csv",
                    b"1543,June Deposit,06/23/26,Credit,1000,11500.00\n")
    _upload_savings(client, acct, "corrected.csv",
                    b"1543,June Deposit Corrected,06/23/26,Credit,1000,11600.00\n")
    assert _balance_of(client, acct) == 11600.00


def test_stale_snapshot_still_logged_to_history(client):
    """balance_history is an append-only audit log — a stale snapshot that loses
    the current_balance arbitration is still recorded at its own as_of."""
    from datetime import date
    from app.models import BalanceHistory
    from tests.conftest import TestingSession

    acct = _make_savings_account(client)
    _upload_savings(client, acct, "june.csv",
                    b"1543,June Deposit,06/23/26,Credit,1000,11500.00\n")
    _upload_savings(client, acct, "april.csv",
                    b"1543,April Withdrawal,04/14/26,Debit,500,9000.00\n")

    db = TestingSession()
    try:
        points = {
            (h.as_of, h.balance)
            for h in db.query(BalanceHistory).filter_by(account_id=acct["id"])
        }
    finally:
        db.close()
    assert points == {
        (date(2026, 6, 23), 11500.00),
        (date(2026, 4, 14), 9000.00),
    }
    assert _balance_of(client, acct) == 11500.00


# ── Binary / parse-failure resilience ────────────────────────────────────────

BINARY_BYTES = b"\xff\xfe\x00binary\x00junk"


def test_binary_upload_returns_200_with_error(client):
    """Binary file (UnicodeDecodeError) produces 200 with imported=0 and a non-empty errors list."""
    acct = _make_account(client)
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", ("bad.xlsx", BINARY_BYTES, "application/octet-stream"))],
    )
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["imported"] == 0
    assert len(results[0]["errors"]) > 0


def test_binary_upload_does_not_persist_transactions(client):
    acct = _make_account(client)
    client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[("files", ("bad.xlsx", BINARY_BYTES, "application/octet-stream"))],
    )
    assert client.get("/api/transactions").json()["total"] == 0


def test_mixed_batch_binary_does_not_abort_good_file(client):
    """One binary + one valid CSV: the good file's rows are persisted, batch returns 200."""
    acct = _make_account(client)
    good_csv = SAMPLE_CSV.read_bytes()
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct["id"]},
        files=[
            ("files", ("bad.xlsx",   BINARY_BYTES, "application/octet-stream")),
            ("files", ("good.csv",   good_csv,     "text/csv")),
        ],
    )
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 2
    bad  = next(x for x in results if x["filename"] == "bad.xlsx")
    good = next(x for x in results if x["filename"] == "good.csv")
    assert bad["imported"] == 0
    assert len(bad["errors"]) > 0
    assert good["imported"] == 13
    assert good["errors"] == []
    assert client.get("/api/transactions").json()["total"] == 13
