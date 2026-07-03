import io
import pathlib

import pytest

from app.importers import IMPORTERS
from app.importers.capitalone import import_checking_savings_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "capitalone_savings_sample.csv"


@pytest.fixture()
def account():
    return Account(
        id=3,
        bank="Capital One",
        name="360 Performance Savings",
        account_number="1543",
        account_type=AccountType.savings,
    )


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "Capital One Checking/Savings" in IMPORTERS
    assert IMPORTERS["Capital One Checking/Savings"] is import_checking_savings_csv


# ── Full parse of sample file ─────────────────────────────────────────────────

def test_parses_all_rows(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert isinstance(result, ImportResult)
    assert len(result.transactions) == 5
    assert result.errors == []


def test_account_id_set_on_all_rows(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert all(tx.account_id == account.id for tx in result.transactions)


# ── Debit → negative, Credit → positive ──────────────────────────────────────

def test_debit_row_is_negative(account, sample_bytes):
    # First row: Withdrawal (Debit) 500
    result = import_checking_savings_csv(sample_bytes, account)
    withdrawal = next(tx for tx in result.transactions if "FID BKG" in tx.description)
    assert withdrawal.amount == -500.0


def test_credit_row_is_positive(account, sample_bytes):
    # Second row: Deposit from 360 Checking (Credit) 20000
    result = import_checking_savings_csv(sample_bytes, account)
    deposit = next(tx for tx in result.transactions if "Deposit from 360 Checking" in tx.description)
    assert deposit.amount == 20000.0


def test_fractional_credit_amount(account, sample_bytes):
    # Row 4: Monthly Interest Paid 339.94
    result = import_checking_savings_csv(sample_bytes, account)
    interest = next(tx for tx in result.transactions if "Monthly Interest" in tx.description)
    assert interest.amount == 339.94


def test_large_debit_amount(account, sample_bytes):
    # Row 5: Withdrawal to 360 Checking 65000
    result = import_checking_savings_csv(sample_bytes, account)
    transfer_out = next(tx for tx in result.transactions if "Withdrawal to 360 Checking" in tx.description)
    assert transfer_out.amount == -65000.0


# ── Balance extraction ────────────────────────────────────────────────────────

def test_account_balance_from_first_row(account, sample_bytes):
    # First row has Balance=151632.53 (most recent)
    result = import_checking_savings_csv(sample_bytes, account)
    assert result.account_balance == 151632.53


def test_account_balance_ascending_date_order(account):
    """Ascending-order export: balance must come from the last (newest) row, not the first."""
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Old Withdrawal,04/14/26,Debit,65000,129869.66\n"
        b"1543,Middle Credit,05/31/26,Credit,339.94,131374.53\n"
        b"1543,Newest Deposit,06/23/26,Credit,500,151632.53\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.account_balance == 151632.53


def test_account_balance_descending_date_order(account):
    """Descending-order export (Capital One default): balance still comes from the newest row."""
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Newest Deposit,06/23/26,Credit,500,151632.53\n"
        b"1543,Middle Credit,05/31/26,Credit,339.94,131374.53\n"
        b"1543,Old Withdrawal,04/14/26,Debit,65000,129869.66\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.account_balance == 151632.53


def test_account_balance_none_on_empty_csv(account):
    csv_bytes = b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.account_balance is None


# ── Default category ──────────────────────────────────────────────────────────

def test_all_rows_default_uncategorized(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert all(tx.category == "Uncategorized" for tx in result.transactions)


# ── Error handling ────────────────────────────────────────────────────────────

def test_invalid_date_skips_row(account):
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Test,not-a-date,Credit,100,5000\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 2" in result.errors[0]


def test_invalid_amount_skips_row(account):
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Test,06/01/26,Credit,abc,5000\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_unknown_transaction_type_skips_row(account):
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Test,06/01/26,Transfer,100,5000\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_partial_success(account):
    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Good Row,06/01/26,Credit,100,5100\n"
        b"1543,Bad Row,not-a-date,Debit,50,5050\n"
        b"1543,Another Good,05/15/26,Debit,200,4850\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1
    assert result.account_balance == 5100.0


def test_bom_stripped(account):
    csv_bytes = (
        b"\xef\xbb\xbfAccount Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Interest,05/31/26,Credit,50.00,10050\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 50.0


# ── import_bulk endpoint updates account.current_balance ─────────────────────

def test_import_bulk_updates_account_balance(client):
    # Create an account
    acc_resp = client.post("/api/accounts", json={
        "bank": "Capital One",
        "name": "360 Savings",
        "account_number": "1543",
        "account_type": "savings",
    })
    assert acc_resp.status_code == 201
    account_id = acc_resp.json()["id"]
    assert acc_resp.json()["current_balance"] is None

    csv_bytes = (
        b"Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n"
        b"1543,Deposit,06/01/26,Credit,1000,51000\n"
        b"1543,Withdrawal,05/15/26,Debit,500,50000\n"
    )

    resp = client.post(
        "/api/transactions/import-bulk",
        data={"account_id": account_id, "importer": "Capital One Checking/Savings"},
        files=[("files", ("savings.csv", io.BytesIO(csv_bytes), "text/csv"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["imported"] == 2
    assert data[0]["errors"] == []

    # Balance should now be updated on the account
    acc_after = client.get(f"/api/accounts").json()
    account = next(a for a in acc_after if a["id"] == account_id)
    assert account["current_balance"] == 51000.0
