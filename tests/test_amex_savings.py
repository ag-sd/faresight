"""American Express Savings importer tests.

Amex savings export: no header row, three columns (Date, Description, Amount),
ISO dates (YYYY-MM-DD), pre-signed amounts. No balance column, so no snapshot.
"""
import pathlib
from datetime import date

import pytest

from app.importers import IMPORTERS
from app.importers.amex import import_savings_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "amex_savings_sample.csv"


@pytest.fixture()
def account():
    return Account(id=20, bank="American Express", name="High Yield Savings",
                   account_number="1234", account_type=AccountType.savings)


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "American Express Savings" in IMPORTERS
    assert IMPORTERS["American Express Savings"] is import_savings_csv


# ── Full parse of sample file ─────────────────────────────────────────────────

def test_parses_all_rows(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    assert isinstance(result, ImportResult)
    assert len(result.transactions) == 5
    assert result.errors == []


def test_account_id_set_on_all_rows(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    assert all(tx.account_id == account.id for tx in result.transactions)


def test_net_delta_known_value(account, sample_bytes):
    # 758.0 - 500.0 + 220.52 + 500.0 + 26000.0 = 26978.52
    result = import_savings_csv(sample_bytes, account)
    assert result.net_delta == 26978.52


# ── Sign convention (amounts are pre-signed; no flip applied) ─────────────────

def test_deposit_stays_positive(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Interest" in tx.description)
    assert tx.amount == 220.52


def test_withdrawal_stays_negative(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "FID BKG" in tx.description)
    assert tx.amount == -500.00


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_iso_date_parsed(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Interest" in tx.description)
    assert tx.date == date(2026, 6, 18)


def test_description_mapped(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    descriptions = [tx.description for tx in result.transactions]
    assert "Interest Payment" in descriptions
    assert "Promotional Bonus" in descriptions


def test_category_is_uncategorized(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    assert all(tx.bank_category == "Uncategorized" for tx in result.transactions)


# ── No balance snapshot ───────────────────────────────────────────────────────

def test_no_snapshot(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    assert result.snapshot is None


# ── All transactions start pending ────────────────────────────────────────────

def test_all_transactions_start_pending(account, sample_bytes):
    result = import_savings_csv(sample_bytes, account)
    assert all(tx.model_confidence == -1 for tx in result.transactions)
    assert all(tx.model_category is None for tx in result.transactions)


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_csv_returns_no_transactions(account):
    result = import_savings_csv(b"", account)
    assert result.transactions == []
    assert result.errors == []
    assert result.snapshot is None


def test_invalid_date_is_an_error(account):
    # No header row → first data row is Row 1
    csv_bytes = b"not-a-date,Test Withdrawal,-100.0\n"
    result = import_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 1" in result.errors[0]


def test_invalid_amount_is_an_error(account):
    csv_bytes = b"2026-01-01,Test Deposit,abc\n"
    result = import_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_partial_success(account):
    csv_bytes = (
        b"2026-06-01,Good Deposit,500.0\n"
        + b"not-a-date,Bad Row,-100.0\n"
        + b"2026-05-01,Another Good,-200.0\n"
    )
    result = import_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1


def test_bom_stripped(account):
    csv_bytes = b"\xef\xbb\xbf2026-06-01,Transfer,758.0\n"
    result = import_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 758.0
