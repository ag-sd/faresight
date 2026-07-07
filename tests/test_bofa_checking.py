"""Bank of America Checking/Savings importer tests.

BofA export: 5-line summary preamble + blank line before the real header.
Columns: Date (m/d/Y), Description, Amount (already signed, comma-formatted),
Running Bal. (comma-formatted balance snapshot). Rows with empty Amount are
"Beginning balance" markers and are skipped silently.
"""
import pathlib
from datetime import date

import pytest

from app.importers import IMPORTERS
from app.importers.bofa import import_checking_savings_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "bofa_checking_sample.csv"

PREAMBLE = (
    b"Description,,Summary Amt.\n"
    b'Beginning balance as of 01/06/2025,,"23,572.28"\n'
    b'Total credits,,"7,536.37"\n'
    b'Total debits,,"-1,325.30"\n'
    b'Ending balance as of 01/21/2025,,"29,783.35"\n'
    b"\n"
)
HEADER = b"Date,Description,Amount,Running Bal.\n"
MARKER = b'01/06/2025,Beginning balance as of 01/06/2025,,"23,572.28"\n'


@pytest.fixture()
def account():
    return Account(id=3, bank="Bank of America", name="Advantage SafeBalance",
                   account_number="1234", account_type=AccountType.checking)


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "Bank of America Checking/Savings" in IMPORTERS
    assert IMPORTERS["Bank of America Checking/Savings"] is import_checking_savings_csv


# ── Full parse of sample file ─────────────────────────────────────────────────

def test_parses_all_rows(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert isinstance(result, ImportResult)
    # 6 data rows: 1 beginning-balance marker skipped, 5 real transactions
    assert len(result.transactions) == 5
    assert result.errors == []


def test_account_id_set_on_all_rows(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert all(tx.account_id == account.id for tx in result.transactions)


def test_net_delta_known_value(account, sample_bytes):
    # -216.15 + 2847.96 + 4688.41 - 1109.15 + 0.00
    result = import_checking_savings_csv(sample_bytes, account)
    assert result.net_delta == 6211.07


# ── Sign convention ───────────────────────────────────────────────────────────

def test_debit_stays_negative(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "VENMO" in tx.description)
    assert tx.amount == -216.15


def test_credit_stays_positive(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "PAYROLL" in tx.description)
    assert tx.amount == 4688.41


def test_commas_stripped_from_large_amount(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Expensify" in tx.description)
    assert tx.amount == 2847.96


def test_zero_amount_row_included(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Fee Waiver" in tx.description)
    assert tx.amount == 0.00


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_date_parsed_from_mdy_four_digit_year(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "VENMO" in tx.description)
    assert tx.date == date(2025, 1, 6)


# ── Balance snapshot ──────────────────────────────────────────────────────────

def test_balance_snapshot_not_none(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert result.snapshot is not None


def test_snapshot_picks_latest_date_row(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert result.snapshot.as_of == date(2025, 1, 21)
    assert result.snapshot.amount == 29783.35


def test_snapshot_not_from_marker_row(account):
    # File with only preamble + header + marker: snapshot must be None
    csv_bytes = PREAMBLE + HEADER + MARKER
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.snapshot is None


# ── All transactions start pending ────────────────────────────────────────────

def test_all_transactions_start_pending(account, sample_bytes):
    result = import_checking_savings_csv(sample_bytes, account)
    assert all(tx.model_confidence == -1 for tx in result.transactions)
    assert all(tx.model_category is None for tx in result.transactions)


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_after_preamble_returns_no_transactions(account):
    csv_bytes = PREAMBLE + HEADER
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert result.errors == []


def test_invalid_amount_is_an_error(account):
    csv_bytes = PREAMBLE + HEADER + MARKER + b'01/15/2025,"Test Charge",not-a-number,"30,000.00"\n'
    result = import_checking_savings_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 9" in result.errors[0]


def test_partial_success(account):
    csv_bytes = (
        PREAMBLE + HEADER + MARKER
        + b'01/10/2025,"Good Row","-100.00","23,472.28"\n'
        + b'bad-date,"Bad Row","-50.00","23,422.28"\n'
        + b'01/12/2025,"Another Good","200.00","23,672.28"\n'
    )
    result = import_checking_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1


def test_bom_stripped(account):
    csv_bytes = b"\xef\xbb\xbf" + PREAMBLE + HEADER + b'01/06/2025,"Coffee","-5.00","23,567.28"\n'
    result = import_checking_savings_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -5.00
