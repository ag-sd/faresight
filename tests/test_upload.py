import pathlib

import pytest

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
    txs = client.get("/api/transactions").json()
    assert len(txs) == 13


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
    assert results[1]["imported"] == 13


def test_import_bulk_multiple_files_all_saved(client):
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
    assert len(client.get("/api/transactions").json()) == 26


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
