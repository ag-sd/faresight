"""Bank of America credit-card importer.

BofA export: m/d/Y Posted Date, a stable Reference Number (bank transaction ID),
a Payee description, an Address we ignore, and an already-signed Amount (charge
negative, payment positive — matching our invariant, so no sign flip). No balance
column, so no snapshot; the balance derives from the accumulated net delta.
"""
import pathlib
from datetime import date

import pytest

from app.importers import IMPORTERS
from app.importers.bofa import import_credit_card_csv
from app.models import Account, AccountType, ImportResult

SAMPLE_CSV = pathlib.Path(__file__).parent / "bofa_sample.csv"
HEADER = b"Posted Date,Reference Number,Payee,Address,Amount\n"


@pytest.fixture()
def account():
    return Account(id=9, bank="Bank of America", name="Customized Cash",
                   account_number="9875", account_type=AccountType.credit_card)


@pytest.fixture()
def sample_bytes():
    return SAMPLE_CSV.read_bytes()


# ── Registry ──────────────────────────────────────────────────────────────────

def test_registered_in_importers():
    assert "Bank of America Credit Card" in IMPORTERS
    assert IMPORTERS["Bank of America Credit Card"] is import_credit_card_csv


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
    # -44.83 + 44.83 - 62.50 - 15.49
    result = import_credit_card_csv(sample_bytes, account)
    assert result.net_delta == -77.99


# ── Sign convention (Amount is already signed — no flip) ──────────────────────

def test_charge_row_stays_negative(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    ins = next(tx for tx in result.transactions if "Lemonade" in tx.description)
    assert ins.amount == -44.83


def test_payment_row_stays_positive(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    pymt = next(tx for tx in result.transactions if tx.description.startswith("PAYMENT FROM CHK"))
    assert pymt.amount == 44.83


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_posted_date_parsed_from_mdy(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    ins = next(tx for tx in result.transactions if "Lemonade" in tx.description)
    assert ins.date == date(2026, 5, 18)


def test_payee_is_description_address_ignored(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    descriptions = [tx.description for tx in result.transactions]
    assert "WHOLEFDS MKT #10234" in descriptions
    # The Address column ("Brooklyn NY ") must not leak into the description.
    assert not any("Brooklyn" in d for d in descriptions)


def test_reference_number_captured(account, sample_bytes):
    result = import_credit_card_csv(sample_bytes, account)
    ins = next(tx for tx in result.transactions if "Lemonade" in tx.description)
    assert ins.reference_number == "24793386136001500770077"


def test_blank_reference_becomes_none(account):
    csv_bytes = HEADER + b'05/01/2026,,"MYSTERY CHARGE","",-9.99\n'
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions[0].reference_number is None


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
    csv_bytes = HEADER + b'not-a-date,24011502125006677330099,"Test","",-10.00\n'
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1
    assert "Row 2" in result.errors[0]


def test_invalid_amount_is_an_error(account):
    csv_bytes = HEADER + b'05/01/2026,24011502125006677330099,"Test","",abc\n'
    result = import_credit_card_csv(csv_bytes, account)
    assert result.transactions == []
    assert len(result.errors) == 1


def test_partial_success(account):
    csv_bytes = (
        HEADER
        + b'05/01/2026,A1,"Good Row","",-10.00\n'
        + b'bad-date,A2,"Bad Row","",-10.00\n'
        + b'05/03/2026,A3,"Another Good","",25.00\n'
    )
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 2
    assert len(result.errors) == 1


def test_bom_stripped(account):
    csv_bytes = b"\xef\xbb\xbf" + HEADER + b'05/01/2026,A1,"Coffee","",-5.00\n'
    result = import_credit_card_csv(csv_bytes, account)
    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -5.00
