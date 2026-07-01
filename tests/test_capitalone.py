import pathlib

import pytest

from app.importers import IMPORTERS
from app.importers.capitalone import import_credit_card_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "capitalone_sample.csv"


@pytest.fixture()
def account():
    return Account(id=7, bank="Capital One", name="Venture", account_number="1543", account_type=AccountType.credit_card)


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "Capital One Credit Card" in IMPORTERS
    assert IMPORTERS["Capital One Credit Card"] is import_credit_card_csv


# ── Full parse of sample file ─────────────────────────────────────────────────

def test_parses_all_rows(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert isinstance(result, ImportResult)
    assert len(result.transactions) == 13
    assert result.errors == []


def test_account_id_set_on_all_rows(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert all(tx.account_id == account.id for tx in result.transactions)


# ── Debit → negative amount ───────────────────────────────────────────────────

def test_debit_row_is_negative(account, sample_bytes):
    # Row 2: SQ *ARTE MUSEUM, Debit=38.11
    result = import_credit_card_csv(sample_bytes, account)
    arte = next(tx for tx in result.transactions if "ARTE MUSEUM" in tx.description)
    assert arte.amount == -38.11


def test_debit_integer_value(account, sample_bytes):
    # Row 4: MTA*NYCT PAYGO, Debit=3
    result = import_credit_card_csv(sample_bytes, account)
    mta = next(tx for tx in result.transactions if tx.description == "MTA*NYCT PAYGO")
    assert mta.amount == -3.0


# ── Credit → positive amount ──────────────────────────────────────────────────

def test_credit_row_is_positive(account, sample_bytes):
    # Row 3: CAPITAL ONE MOBILE PYMT, Credit=139.85
    result = import_credit_card_csv(sample_bytes, account)
    pymt = next(tx for tx in result.transactions if tx.description == "CAPITAL ONE MOBILE PYMT")
    assert pymt.amount == 139.85


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_transaction_date_used_not_posted_date(account, sample_bytes):
    # Row 2: Transaction Date=2026-06-17, Posted Date=2026-06-18
    from datetime import date
    result = import_credit_card_csv(sample_bytes, account)
    arte = next(tx for tx in result.transactions if "ARTE MUSEUM" in tx.description)
    assert arte.date == date(2026, 6, 17)


def test_description_preserved(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    descriptions = [tx.description for tx in result.transactions]
    assert "SQ *ARTE MUSEUM: AN IMMER" in descriptions
    assert "NYTIMES DISC*" in descriptions


def test_category_preserved(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    arte = next(tx for tx in result.transactions if "ARTE MUSEUM" in tx.description)
    assert arte.category == "Entertainment"


# ── Payment classification ────────────────────────────────────────────────────

def test_mobile_payment_classified_as_transfers(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    payments = [tx for tx in result.transactions if tx.description == "CAPITAL ONE MOBILE PYMT"]
    assert len(payments) == 2
    assert all(tx.model_category == "Transfers & Fees" for tx in payments)
    assert all(tx.model_confidence == 10 for tx in payments)


def test_autopay_payment_classified_as_transfers(account):
    csv_bytes = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-15,2026-01-16,1234,CAPITAL ONE AUTOPAY PYMT,Payment/Credit,,250.00\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.model_category == "Transfers & Fees"
    assert tx.model_confidence == 10


def test_regular_transaction_not_classified_as_transfers(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    arte = next(tx for tx in result.transactions if "ARTE MUSEUM" in tx.description)
    assert arte.model_category is None
    assert arte.model_confidence == -1


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_csv_returns_no_transactions(account):
    csv_bytes = b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert result.errors == []


def test_row_with_neither_debit_nor_credit_is_an_error(account):
    csv_bytes = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-01,2026-01-02,1234,Test,Food,,\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 2" in result.errors[0]


def test_invalid_date_skips_row(account):
    csv_bytes = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"not-a-date,2026-01-02,1234,Test,Food,10.00,\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_invalid_amount_skips_row(account):
    csv_bytes = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-01,2026-01-02,1234,Test,Food,abc,\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_partial_success(account):
    csv_bytes = (
        b"Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-01,2026-01-02,1234,Good Row,Food,10.00,\n"
        b"bad-date,2026-01-02,1234,Bad Row,Food,10.00,\n"
        b"2026-01-03,2026-01-04,1234,Another Good,Dining,,25.00\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1


def test_bom_stripped(account):
    csv_bytes = (
        b"\xef\xbb\xbfTransaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
        b"2026-01-01,2026-01-02,1234,Coffee,Dining,5.00,\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -5.00
