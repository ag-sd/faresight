"""Chase credit-card importer tests.

Chase export: Transaction Date (m/d/Y), Post Date (ignored), Description,
Category (ignored), Type (Sale/Payment/Return), already-signed Amount, Memo
(ignored). No Reference Number column. No balance snapshot.
"""
import pathlib
from datetime import date

import pytest

from app.importers import IMPORTERS
from app.importers.chase import import_credit_card_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "chase_sample.csv"
HEADER = b"Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"


@pytest.fixture()
def account():
    return Account(id=7, bank="Chase", name="Sapphire Preferred",
                   account_number="2621", account_type=AccountType.credit_card)


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "Chase Credit Card" in IMPORTERS
    assert IMPORTERS["Chase Credit Card"] is import_credit_card_csv


# ── Full parse of sample file ─────────────────────────────────────────────────

def test_parses_all_rows(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert isinstance(result, ImportResult)
    assert len(result.transactions) == 4
    assert result.errors == []


def test_account_id_set_on_all_rows(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert all(tx.account_id == account.id for tx in result.transactions)


def test_no_snapshot_for_credit_card(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert result.snapshot is None


def test_net_delta_known_value(account, sample_bytes):
    # -26.11 + 1229.02 + 4.40 - 122.26 = 1085.05
    result = import_credit_card_csv(sample_bytes, account)
    assert result.net_delta == 1085.05


# ── Sign convention (Amount is already signed — no flip) ──────────────────────

def test_charge_row_stays_negative(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "AMAZON MKTPL" in tx.description)
    assert tx.amount == -26.11


def test_payment_row_stays_positive(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "Payment Thank You" in tx.description)
    assert tx.amount == 1229.02


def test_return_row_stays_positive(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "AMAZON MKTPLACE PMTS" in tx.description)
    assert tx.amount == 4.40


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_transaction_date_parsed_from_mdy(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "AMAZON MKTPL" in tx.description)
    assert tx.date == date(2026, 7, 2)


def test_description_mapped(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    descriptions = [tx.description for tx in result.transactions]
    assert "AMAZON MKTPL*578VW3QT3" in descriptions
    assert "GRUBHUB*BHATTIINDIANGR" in descriptions


def test_no_reference_number(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    assert all(tx.reference_number is None for tx in result.transactions)


def test_bank_category_preserved(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    tx = next(tx for tx in result.transactions if "AMAZON MKTPL" in tx.description)
    assert tx.category == "Shopping"


def test_empty_category_falls_back_to_uncategorized(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    # Payment Thank You-Mobile has no Category in the Chase export
    tx = next(tx for tx in result.transactions if "Payment Thank You" in tx.description)
    assert tx.category == "Uncategorized"


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


def test_invalid_date_is_an_error(account):
    csv_bytes = HEADER + b"not-a-date,07/03/2026,Test Purchase,Shopping,Sale,-10.00,\n"
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 2" in result.errors[0]


def test_invalid_amount_is_an_error(account):
    csv_bytes = HEADER + b"07/02/2026,07/03/2026,Test Purchase,Shopping,Sale,abc,\n"
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_partial_success(account):
    csv_bytes = (
        HEADER
        + b"07/01/2026,07/02/2026,Good Row,Shopping,Sale,-10.00,\n"
        + b"bad-date,07/03/2026,Bad Row,Shopping,Sale,-10.00,\n"
        + b"07/03/2026,07/04/2026,Another Good,Food & Drink,Sale,-25.00,\n"
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1


def test_bom_stripped(account):
    csv_bytes = b"\xef\xbb\xbf" + HEADER + b"07/02/2026,07/03/2026,Coffee,Food & Drink,Sale,-5.00,\n"
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -5.00
