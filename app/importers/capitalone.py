from dataclasses import dataclass
from datetime import date as date_type, datetime
from typing import Optional

from app.importers.base import CsvImporter
from app.models import Account, BalanceSnapshot
from app.schemas import TransactionCreate


class CapitalOneCreditCard(CsvImporter[dict]):
    """Capital One credit-card export: separate Debit/Credit columns, per-row
    Category, ISO dates, no balance column."""

    def parse_row(self, row: dict, account: Account, ctx: dict) -> TransactionCreate:
        tx_date = date_type.fromisoformat(row["Transaction Date"].strip())
        description = row["Description"].strip()
        category = row["Category"].strip() or "Uncategorized"
        amount = self.signed_amount(row["Debit"].strip(), row["Credit"].strip())

        return TransactionCreate(
            date=tx_date,
            description=description,
            amount=round(amount, 2),
            category=category,
            account_id=account.id,
        )


@dataclass
class _BalanceCtx:
    balance: Optional[float] = None
    latest: Optional[date_type] = None


class CapitalOneCheckingSavings(CsvImporter[_BalanceCtx]):
    """Capital One checking/savings export: a Transaction Type discriminator,
    m/d/y dates, and an authoritative per-row Balance column."""

    def new_context(self) -> _BalanceCtx:
        return _BalanceCtx()

    def parse_row(self, row: dict, account: Account, ctx: _BalanceCtx) -> TransactionCreate:
        tx_date = datetime.strptime(row["Transaction Date"].strip(), "%m/%d/%y").date()
        description = row["Transaction Description"].strip()
        tx_type = row["Transaction Type"].strip()
        raw_amount = float(row["Transaction Amount"].strip())

        if tx_type == "Debit":
            is_debit = True
        elif tx_type == "Credit":
            is_debit = False
        else:
            raise ValueError(f"unknown Transaction Type: {tx_type!r}")
        amount = self.apply_sign(raw_amount, debit=is_debit)

        # Track the balance stated on the newest row seen so far.
        if ctx.latest is None or tx_date >= ctx.latest:
            ctx.latest = tx_date
            ctx.balance = float(row["Balance"].strip())

        return TransactionCreate(
            date=tx_date,
            description=description,
            amount=round(amount, 2),
            category="Uncategorized",
            account_id=account.id,
        )

    def balance_snapshot(self, ctx: _BalanceCtx) -> Optional[BalanceSnapshot]:
        if ctx.balance is None or ctx.latest is None:
            return None
        return BalanceSnapshot(amount=ctx.balance, as_of=ctx.latest)


# ── Registry-facing wrapper functions ────────────────────────────────────────
# The registry in app/importers/__init__.py maps display names to these; keeping
# them as module-level callables preserves the (file_bytes, account) contract.

def import_credit_card_csv(file_bytes, account, filename=None, importer=None):
    return CapitalOneCreditCard().run(file_bytes, account, filename, importer)


def import_checking_savings_csv(file_bytes, account, filename=None, importer=None):
    return CapitalOneCheckingSavings().run(file_bytes, account, filename, importer)
