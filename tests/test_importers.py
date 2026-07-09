"""Tests for the CsvImporter Template-Method base class, exercised through a tiny
fake importer so the behaviour is isolated from any real bank format."""
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pytest

from app.importers.base import CsvImporter
from app.models import Account, AccountType, BalanceSnapshot, ImportResult
from app.schemas import TransactionCreate


@pytest.fixture()
def account():
    return Account(id=42, bank="Test Bank", name="Test", account_number="0001",
                   account_type=AccountType.checking)


# ── A minimal importer: one "amount" column, optional "skip" flag ────────────

class FakeImporter(CsvImporter[dict]):
    def parse_row(self, row, account, ctx) -> Optional[TransactionCreate]:
        if row["skip"].strip() == "yes":
            return None
        return TransactionCreate(
            date=date.fromisoformat(row["date"].strip()),
            description=row["desc"].strip(),
            amount=round(float(row["amount"].strip()), 2),
            bank_category="Uncategorized",
            account_id=account.id,
        )


def _csv(*rows: str) -> bytes:
    header = "date,desc,amount,skip\n"
    return (header + "".join(r + "\n" for r in rows)).encode("utf-8")


# ── parse_row returning None skips the row (no error) ────────────────────────

def test_none_row_is_skipped_without_error(account):
    result = FakeImporter().run(_csv(
        "2026-01-01,Keep,10.00,no",
        "2026-01-02,Drop,99.00,yes",
        "2026-01-03,Keep,5.00,no",
    ), account)
    assert isinstance(result, ImportResult)
    assert [tx.description for tx in result.transactions] == ["Keep", "Keep"]
    assert result.errors == []


# ── net_delta is the rounded sum of parsed amounts ───────────────────────────

def test_net_delta_sum(account):
    result = FakeImporter().run(_csv(
        "2026-01-01,A,10.005,no",
        "2026-01-02,B,-3.00,no",
        "2026-01-03,Skipped,1000.00,yes",   # excluded from the sum
    ), account)
    assert result.net_delta == round(10.005 - 3.00, 2)


# ── row errors are captured with a 1-based (start=2) line number ─────────────

def test_bad_row_recorded_as_error(account):
    result = FakeImporter().run(_csv(
        "2026-01-01,Good,10.00,no",
        "not-a-date,Bad,10.00,no",
    ), account)
    assert len(result.transactions) == 1
    assert len(result.errors) == 1
    assert "Row 3" in result.errors[0]


# ── identity (account_id, filename, importer) stamped by run() ───────────────

def test_identity_stamped_on_result(account):
    result = FakeImporter().run(
        _csv("2026-01-01,A,1.00,no"),
        account, filename="jan.csv", importer="Fake Bank",
    )
    assert result.account_id == 42
    assert result.filename == "jan.csv"
    assert result.importer == "Fake Bank"


def test_identity_defaults_when_omitted(account):
    result = FakeImporter().run(_csv("2026-01-01,A,1.00,no"), account)
    assert result.account_id == 42
    assert result.filename is None
    assert result.importer is None


# ── default hooks: no context, no snapshot ───────────────────────────────────

def test_default_context_is_empty_dict():
    assert FakeImporter().new_context() == {}


def test_default_no_snapshot(account):
    result = FakeImporter().run(_csv("2026-01-01,A,1.00,no"), account)
    assert result.snapshot is None


# ── apply_sign primitive (single home of the sign convention) ────────────────

def test_apply_sign_debit_negative():
    assert CsvImporter.apply_sign(38.11, debit=True) == -38.11


def test_apply_sign_credit_positive():
    assert CsvImporter.apply_sign(139.85, debit=False) == 139.85


# ── signed_amount helper (column format; delegates to apply_sign) ────────────

def test_signed_amount_debit_negative():
    assert CsvImporter.signed_amount("38.11", "") == -38.11


def test_signed_amount_credit_positive():
    assert CsvImporter.signed_amount("", "139.85") == 139.85


def test_signed_amount_neither_raises():
    with pytest.raises(ValueError):
        CsvImporter.signed_amount("", "")


# ── snapshot-newer-wins via a stateful context importer ──────────────────────

@dataclass
class _Ctx:
    balance: Optional[float] = None
    latest: Optional[date] = None


class SnapshotImporter(CsvImporter[_Ctx]):
    def new_context(self) -> _Ctx:
        return _Ctx()

    def parse_row(self, row, account, ctx: _Ctx):
        d = date.fromisoformat(row["date"].strip())
        bal = float(row["amount"].strip())
        if ctx.latest is None or d >= ctx.latest:
            ctx.latest, ctx.balance = d, bal
        return TransactionCreate(date=d, description=row["desc"].strip(),
                                 amount=bal, bank_category="Uncategorized",
                                 account_id=account.id)

    def balance_snapshot(self, ctx: _Ctx) -> Optional[BalanceSnapshot]:
        if ctx.balance is None:
            return None
        return BalanceSnapshot(amount=ctx.balance, as_of=ctx.latest)


def test_snapshot_picks_newest_regardless_of_order(account):
    ascending = SnapshotImporter().run(_csv(
        "2026-01-01,old,100,no",
        "2026-03-01,new,300,no",
    ), account)
    descending = SnapshotImporter().run(_csv(
        "2026-03-01,new,300,no",
        "2026-01-01,old,100,no",
    ), account)
    assert ascending.snapshot == BalanceSnapshot(300.0, date(2026, 3, 1))
    assert descending.snapshot == BalanceSnapshot(300.0, date(2026, 3, 1))


# ── parse_row is unit-testable in isolation with a hand-built context ────────

def test_parse_row_in_isolation(account):
    ctx = _Ctx()
    tx = SnapshotImporter().parse_row(
        {"date": "2026-05-02", "desc": "Interest", "amount": "12.34"},
        account, ctx,
    )
    assert tx.amount == 12.34
    assert tx.account_id == 42
    assert ctx.balance == 12.34
    assert ctx.latest == date(2026, 5, 2)
