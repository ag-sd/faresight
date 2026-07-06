"""Re-import idempotency guard (P2) + net_delta balance application (P3).

Layer 1: exact-file SHA-256 short-circuit on FileImport.content_hash.
Layer 2: row-level occurrence counting on Transaction.dedup_hash — legitimate
duplicates (two identical bus fares in one file) import; re-imports and
overlapping exports contribute only their genuinely new rows.
"""
import pathlib
from datetime import date

from app.models import BalanceHistory
from tests.conftest import TestingSession, make_tx

SAMPLE_CSV = pathlib.Path(__file__).parent / "capitalone_sample.csv"
CAPONE_IMPORTER = "Capital One Credit Card"
SAVINGS_IMPORTER = "Capital One Checking/Savings"

CC_HEADER = b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
# Net of the 13 sample rows: credits 139.85 + 46.27, debits 193.98 total.
SAMPLE_NET = -7.86


def _make_account(client, account_type="credit_card", name="Venture"):
    r = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": name,
        "account_number": "1543",
        "account_type": account_type,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _import(client, acct_id, files, importer=CAPONE_IMPORTER):
    r = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": acct_id, "importer": importer},
        files=[("files", (name, content, "text/csv")) for name, content in files],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _balance(client, acct_id):
    acc = next(a for a in client.get("/api/accounts").json() if a["id"] == acct_id)
    return acc["current_balance"]


def _history(acct_id):
    """balance_history rows for an account, in insertion order. Read via a fresh
    session (no read endpoint exists — P4 is groundwork only)."""
    db = TestingSession()
    try:
        return (
            db.query(BalanceHistory)
            .filter(BalanceHistory.account_id == acct_id)
            .order_by(BalanceHistory.id)
            .all()
        )
    finally:
        db.close()


# ── Legitimate duplicates survive ─────────────────────────────────────────────

def test_identical_rows_within_one_file_both_import(client):
    """The sample has two identical NJT RAIL rows (same day, same amount) —
    occurrence counting must keep both on first import."""
    acct = _make_account(client)
    results = _import(client, acct["id"], [("sample.csv", SAMPLE_CSV.read_bytes())])
    assert results[0]["imported"] == 13
    assert results[0]["skipped"] == 0
    txs = client.get("/api/transactions?limit=100").json()["data"]
    njt = [t for t in txs if t["description"] == "NJT RAIL MY-TIX" and t["date"] == "2025-06-28"]
    assert len(njt) == 2


# ── Layer 1: exact-file re-upload ─────────────────────────────────────────────

def test_exact_reimport_is_idempotent(client):
    acct = _make_account(client)
    csv_bytes = SAMPLE_CSV.read_bytes()
    _import(client, acct["id"], [("sample.csv", csv_bytes)])
    balance_before = _balance(client, acct["id"])

    results = _import(client, acct["id"], [("sample.csv", csv_bytes)])
    assert results[0]["duplicate_file"] is True
    assert results[0]["imported"] == 0
    assert client.get("/api/transactions").json()["total"] == 13
    assert _balance(client, acct["id"]) == balance_before


def test_failed_import_is_retryable(client):
    """rows_persisted == 0 records (e.g. parse failures) never trigger the
    exact-duplicate short-circuit."""
    acct = _make_account(client)
    binary = b"\xff\xfe\x00junk"
    first = _import(client, acct["id"], [("bad.csv", binary)])
    assert first[0]["imported"] == 0 and first[0]["errors"]
    second = _import(client, acct["id"], [("bad.csv", binary)])
    assert "duplicate_file" not in second[0]


# ── Layer 2: overlapping exports ──────────────────────────────────────────────

def test_overlapping_file_imports_only_new_rows(client):
    """An extended export (all old rows + one new) contributes only the new row."""
    acct = _make_account(client)
    _import(client, acct["id"], [("june.csv", SAMPLE_CSV.read_bytes())])
    balance_before = _balance(client, acct["id"])

    extended = SAMPLE_CSV.read_bytes() + b"2026-06-20,2026-06-21,1234,NEW COFFEE SHOP,Dining,4.50,\n"
    results = _import(client, acct["id"], [("july.csv", extended)])
    assert results[0]["imported"] == 1
    assert results[0]["skipped"] == 13
    assert client.get("/api/transactions").json()["total"] == 14
    assert _balance(client, acct["id"]) == round(balance_before - 4.50, 2)


def test_multiset_inserts_only_the_excess_copy(client):
    """DB holds one copy, the new file holds two → exactly one more inserts."""
    acct = _make_account(client)
    row = b"2026-03-01,2026-03-02,1234,MTA*NYCT PAYGO,Transit,3.00,\n"
    _import(client, acct["id"], [("one.csv", CC_HEADER + row)])

    results = _import(client, acct["id"], [("two.csv", CC_HEADER + row + row)])
    assert results[0]["imported"] == 1
    assert results[0]["skipped"] == 1
    assert client.get("/api/transactions").json()["total"] == 2


def test_db_holding_more_copies_than_file_inserts_nothing(client):
    acct = _make_account(client)
    row = b"2026-03-01,2026-03-02,1234,MTA*NYCT PAYGO,Transit,3.00,\n"
    _import(client, acct["id"], [("two.csv", CC_HEADER + row + row)])

    results = _import(client, acct["id"], [("one.csv", CC_HEADER + row)])
    assert results[0]["imported"] == 0
    assert results[0]["skipped"] == 1
    assert client.get("/api/transactions").json()["total"] == 2


def test_manual_transaction_dedupes_later_import(client):
    """A hand-entered transaction carries the identity hash, so importing the
    bank CSV containing it does not duplicate it."""
    acct = _make_account(client)
    make_tx(client, date="2026-03-01", description="MTA*NYCT PAYGO",
            amount=-3.00, account_id=acct["id"])

    row = b"2026-03-01,2026-03-02,1234,MTA*NYCT PAYGO,Transit,3.00,\n"
    results = _import(client, acct["id"], [("march.csv", CC_HEADER + row)])
    assert results[0]["imported"] == 0
    assert results[0]["skipped"] == 1
    assert client.get("/api/transactions").json()["total"] == 1


def test_edited_row_is_not_reimported(client):
    """dedup_hash is stamped at insert and never recomputed, so editing a row
    doesn't let a later overlapping import re-insert the original."""
    acct = _make_account(client)
    row = b"2026-03-01,2026-03-02,1234,MTA*NYCT PAYGO,Transit,3.00,\n"
    _import(client, acct["id"], [("one.csv", CC_HEADER + row)])
    tx = client.get("/api/transactions").json()["data"][0]
    client.patch(f"/api/transactions/{tx['id']}", json={"description": "Bus fare (renamed)"})

    extended = CC_HEADER + row + b"2026-03-05,2026-03-06,1234,NEW ROW,Dining,7.00,\n"
    results = _import(client, acct["id"], [("two.csv", extended)])
    assert results[0]["imported"] == 1
    assert results[0]["skipped"] == 1
    assert client.get("/api/transactions").json()["total"] == 2


# ── P3: balance application ───────────────────────────────────────────────────

def test_credit_card_balance_derives_from_inserted_rows(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("sample.csv", SAMPLE_CSV.read_bytes())])
    assert _balance(client, acct["id"]) == SAMPLE_NET


def test_balance_accumulates_across_distinct_files(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("a.csv", CC_HEADER + b"2026-01-01,2026-01-02,1234,A,Food,5.00,\n")])
    _import(client, acct["id"], [("b.csv", CC_HEADER + b"2026-02-01,2026-02-02,1234,B,Food,10.00,\n")])
    assert _balance(client, acct["id"]) == -15.00


def test_empty_file_leaves_balance_untouched(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("empty.csv", CC_HEADER)])
    assert _balance(client, acct["id"]) is None


def test_snapshot_still_wins_over_delta(client):
    """Snapshot-bearing files (checking/savings) keep the authoritative path."""
    acct = _make_account(client, account_type="savings", name="360 Savings")
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Deposit,06/23/26,Credit,1000,11500.00\n"
    )
    _import(client, acct["id"], [("sav.csv", csv_bytes)], importer=SAVINGS_IMPORTER)
    assert _balance(client, acct["id"]) == 11500.00

    results = _import(client, acct["id"], [("sav.csv", csv_bytes)], importer=SAVINGS_IMPORTER)
    assert results[0]["duplicate_file"] is True
    assert _balance(client, acct["id"]) == 11500.00


# ── P4: balance_history logging ───────────────────────────────────────────────

def test_delta_import_logs_one_balance_point(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("sample.csv", SAMPLE_CSV.read_bytes())])
    rows = _history(acct["id"])
    assert len(rows) == 1
    assert rows[0].balance == SAMPLE_NET
    # as_of is the newest transaction that produced the balance.
    latest = max(t["date"] for t in client.get("/api/transactions").json()["data"])
    assert rows[0].as_of.isoformat() == latest


def test_snapshot_import_logs_authoritative_point(client):
    acct = _make_account(client, account_type="savings", name="360 Savings")
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Deposit,06/23/26,Credit,1000,11500.00\n"
    )
    _import(client, acct["id"], [("sav.csv", csv_bytes)], importer=SAVINGS_IMPORTER)
    rows = _history(acct["id"])
    assert len(rows) == 1
    assert rows[0].balance == 11500.00
    assert rows[0].as_of == date(2026, 6, 23)  # the file's stated snapshot date


def test_reimport_logs_no_new_balance_point(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("sample.csv", SAMPLE_CSV.read_bytes())])
    _import(client, acct["id"], [("sample.csv", SAMPLE_CSV.read_bytes())])  # Layer-1 duplicate
    assert len(_history(acct["id"])) == 1


def test_overlapping_file_logs_single_new_point(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("june.csv", SAMPLE_CSV.read_bytes())])
    extended = SAMPLE_CSV.read_bytes() + b"2026-06-20,2026-06-21,1234,NEW COFFEE SHOP,Dining,4.50,\n"
    _import(client, acct["id"], [("july.csv", extended)])
    rows = _history(acct["id"])
    assert len(rows) == 2  # first import + the one genuinely-new row
    assert rows[-1].balance == round(SAMPLE_NET - 4.50, 2)
    assert rows[-1].as_of == date(2026, 6, 20)


def test_empty_file_logs_no_balance_point(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("empty.csv", CC_HEADER)])
    assert _history(acct["id"]) == []


def test_two_files_log_two_points_in_order(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("a.csv", CC_HEADER + b"2026-01-01,2026-01-02,1234,A,Food,5.00,\n")])
    _import(client, acct["id"], [("b.csv", CC_HEADER + b"2026-02-01,2026-02-02,1234,B,Food,10.00,\n")])
    rows = _history(acct["id"])
    assert [r.as_of for r in rows] == [date(2026, 1, 1), date(2026, 2, 1)]
    assert [r.balance for r in rows] == [-5.00, -15.00]


# ── Bookkeeping surface ───────────────────────────────────────────────────────

def test_rows_skipped_reported_in_file_imports(client):
    acct = _make_account(client)
    _import(client, acct["id"], [("june.csv", SAMPLE_CSV.read_bytes())])
    extended = SAMPLE_CSV.read_bytes() + b"2026-06-20,2026-06-21,1234,NEW COFFEE SHOP,Dining,4.50,\n"
    _import(client, acct["id"], [("july.csv", extended)])

    data = client.get("/api/file-imports").json()["data"]
    july = next(f for f in data if f["filename"] == "july.csv")
    assert july["rows_persisted"] == 1
    assert july["rows_skipped"] == 13
    assert july["rows_seen"] == 14
