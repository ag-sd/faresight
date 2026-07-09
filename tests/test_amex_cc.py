"""American Express Credit Card importer tests.

Amex CC export: m/d/Y dates, 11-column header, sign-inverted Amount (purchases
positive in file → flip to negative), single-quote-wrapped Reference, and a
Category column passed through as a hint.
"""
import pathlib
from datetime import date

import pytest

from app.importers import IMPORTERS
from app.importers.amex import import_credit_card_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "amex_cc_sample.csv"
# 7 commas between Amount and Reference to skip the 6 unused columns
# (Extended Details, Appears On, Address, City/State, Zip Code, Country)
HEADER = (
    b"Date,Description,Amount,Extended Details,Appears On Your Statement As,"
    b"Address,City/State,Zip Code,Country,Reference,Category\n"
)
# Convenience: minimal row with all 11 fields (unused columns left empty)
ROW = b"07/01/2026,Test Charge,10.00,,,,,,,'320261000000000001',Groceries\n"


@pytest.fixture()
def account():
    return Account(id=21, bank="American Express", name="Blue Cash Preferred",
                   account_number="5678", account_type=AccountType.credit_card)


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "American Express Credit Card" in IMPORTERS
    assert IMPORTERS["American Express Credit Card"] is import_credit_card_csv


# ── Full parse of sample file ─────────────────────────────────────────────────

def test_parses_all_rows(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert isinstance(result, ImportResult)
    assert len(result.transactions) == 5
    assert result.errors == []


def test_account_id_set_on_all_rows(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert all(tx.account_id == account.id for tx in result.transactions)


def test_no_snapshot(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert result.snapshot is None


def test_net_delta_known_value(account, sample_bytes):
    # -25.84 - 3.00 + 1220.17 + 30.00 + 16.20 = 1237.53
    result = import_credit_card_csv(sample_bytes, account)
    assert result.net_delta == 1237.53


# ── Sign convention (Amex exports purchases as positive → flip to negative) ───

def test_purchase_flipped_to_negative(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "PENNY LICK" in tx.description)
    assert tx.amount == -25.84


def test_payment_flipped_to_positive(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "MOBILE PAYMENT" in tx.description)
    assert tx.amount == 1220.17


def test_return_credit_flipped_to_positive(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "TYRWHITT" in tx.description)
    assert tx.amount == 30.00


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_mdy_date_parsed(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "PENNY LICK" in tx.description)
    assert tx.date == date(2026, 7, 5)


def test_reference_stripped_of_single_quotes(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "PENNY LICK" in tx.description)
    assert tx.reference_number == "320261860393281369"


def test_empty_reference_becomes_none(account):
    # Row with an empty Reference field
    csv_bytes = HEADER + b"07/01/2026,Empty Ref Test,10.00,,,,,,,,Groceries\n"
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions[0].reference_number is None


def test_category_preserved(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "PENNY LICK" in tx.description)
    assert tx.bank_category == "Restaurant-Bar & Café"


def test_empty_category_falls_back_to_uncategorized(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "MOBILE PAYMENT" in tx.description)
    assert tx.bank_category == "Uncategorized"


# ── All transactions start pending ────────────────────────────────────────────

def test_all_transactions_start_pending(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert all(tx.model_confidence == -1 for tx in result.transactions)
    assert all(tx.model_category is None for tx in result.transactions)


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_csv_returns_no_transactions(account):
    result = import_credit_card_csv(HEADER, account)
    assert result.transactions == []
    assert result.errors == []
    assert result.snapshot is None


def test_invalid_date_is_an_error(account):
    csv_bytes = HEADER + b"not-a-date,Bad Row,10.00,,,,,,,'320261000001',Groceries\n"
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 2" in result.errors[0]


def test_invalid_amount_is_an_error(account):
    csv_bytes = HEADER + b"07/01/2026,Bad Amount,abc,,,,,,,'320261000001',Groceries\n"
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_partial_success(account):
    csv_bytes = (
        HEADER
        + b"07/01/2026,Good Charge,25.00,,,,,,,'320261000001',Groceries\n"
        + b"not-a-date,Bad Row,10.00,,,,,,,'320261000002',Groceries\n"
        + b"06/01/2026,Another Good,50.00,,,,,,,'320261000003',Transport\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1


def test_bom_stripped(account):
    csv_bytes = b"\xef\xbb\xbf" + HEADER + ROW
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -10.00
