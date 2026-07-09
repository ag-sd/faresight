"""SoFi Checking/Savings importer tests.

SoFi export: ISO dates (YYYY-MM-DD), pre-signed Amount (withdrawals negative),
Current balance column for the snapshot. No preamble, no reference number.
"""
import pathlib
from datetime import date

import pytest

from app.importers import IMPORTERS
from app.importers.sofi import import_checking_savings_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "sofi_sample.csv"
HEADER = b"Date,Description,Type,Amount,Current balance,Status\n"


@pytest.fixture()
def account():
    return Account(id=11, bank="SoFi", name="Checking",
                   account_number="5995", account_type=AccountType.checking)


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "SoFi Checking/Savings" in IMPORTERS
    assert IMPORTERS["SoFi Checking/Savings"] is import_checking_savings_csv


# ── Full parse of sample file ─────────────────────────────────────────────────

def test_parses_all_rows(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert isinstance(result, ImportResult)
    assert len(result.transactions) == 4
    assert result.errors == []


def test_account_id_set_on_all_rows(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert all(tx.account_id == account.id for tx in result.transactions)


def test_net_delta_known_value(account, sample_bytes):
    # 0.33 + 501 - 1270 + 50 = -718.67
    result = import_checking_savings_csv(sample_bytes, account)
    assert result.net_delta == -718.67


# ── Sign convention (Amount is already signed) ────────────────────────────────

def test_withdrawal_stays_negative(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Savings" in tx.description)
    assert tx.amount == -1270.00


def test_deposit_stays_positive(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Institutional" in tx.description)
    assert tx.amount == 501.00


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_iso_date_parsed(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Interest" in tx.description)
    assert tx.date == date(2026, 6, 30)


def test_description_mapped(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    descriptions = [tx.description for tx in result.transactions]
    assert "Money Promo Bonus" in descriptions
    assert "Interest earned" in descriptions


def test_type_used_as_category(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Interest" in tx.description)
    assert tx.bank_category == "INTEREST_EARNED"
    tx = next(tx for tx in result.transactions if "Institutional" in tx.description)
    assert tx.bank_category == "DIRECT_DEPOSIT"


# ── Balance snapshot ──────────────────────────────────────────────────────────

def test_balance_snapshot_not_none(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert result.snapshot is not None


def test_snapshot_picks_latest_date_row(account, sample_bytes):
    # Rows are in reverse-chrono order; snapshot must be the max-date row
    result = import_checking_savings_csv(sample_bytes, account)
    assert result.snapshot.as_of == date(2026, 6, 30)
    assert result.snapshot.amount == 1231.02


def test_snapshot_uses_max_date_not_first_row(account):
    # Feed rows in ascending order — snapshot should still be the latest date
    csv_bytes = (
        HEADER
        + b"2024-07-07,Money Promo Bonus,BONUS,50,50.18,Posted\n"
        + b"2026-06-30,Interest earned,INTEREST_EARNED,0.33,1231.02,Posted\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.snapshot.as_of == date(2026, 6, 30)
    assert result.snapshot.amount == 1231.02


# ── All transactions start pending ────────────────────────────────────────────

def test_all_transactions_start_pending(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert all(tx.model_confidence == -1 for tx in result.transactions)
    assert all(tx.model_category is None for tx in result.transactions)


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_csv_returns_no_transactions(account):
    result = import_checking_savings_csv(HEADER, account)
    assert result.transactions == []
    assert result.errors == []
    assert result.snapshot is None


def test_invalid_date_is_an_error(account):
    csv_bytes = HEADER + b"not-a-date,Test,WITHDRAWAL,-10,100.00,Posted\n"
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 2" in result.errors[0]


def test_invalid_amount_is_an_error(account):
    csv_bytes = HEADER + b"2026-01-01,Test,WITHDRAWAL,abc,100.00,Posted\n"
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_partial_success(account):
    csv_bytes = (
        HEADER
        + b"2026-06-01,Good Row,DIRECT_DEPOSIT,500,1500.00,Posted\n"
        + b"bad-date,Bad Row,WITHDRAWAL,-100,1400.00,Posted\n"
        + b"2026-05-01,Another Good,WITHDRAWAL,-200,1200.00,Posted\n"
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1


def test_bom_stripped(account):
    csv_bytes = b"\xef\xbb\xbf" + HEADER + b"2026-06-01,Coffee,WITHDRAWAL,-5.00,995.00,Posted\n"
    result = import_checking_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -5.00
