from dataclasses import dataclass
from datetime import date as date_type, datetime
from typing import Optional

from app.importers.base import CsvImporter
from app.models import Account, BalanceSnapshot
from app.schemas import TransactionCreate


class BankOfAmericaCreditCard(CsvImporter[dict]):
    """Bank of America credit-card export: an m/d/Y Posted Date, a stable
    Reference Number (bank transaction ID), a Payee description, an Address we
    ignore, and an already-signed Amount (charge negative, payment positive —
    matching our invariant, so no sign flip). No balance column, so no snapshot;
    the credit-card balance derives from the accumulated net delta."""

    def parse_row(self, row: dict, account: Account, ctx: dict) -> TransactionCreate:
        tx_date = datetime.strptime(row["Posted Date"].strip(), "%m/%d/%Y").date()
        description = row["Payee"].strip()
        amount = round(float(row["Amount"].strip()), 2)
        reference = row["Reference Number"].strip() or None

        return TransactionCreate(
            date=tx_date,
            description=description,
            amount=amount,
            category="Uncategorized",
            account_id=account.id,
            reference_number=reference,
        )


# ── Registry-facing wrapper ───────────────────────────────────────────────────

def import_credit_card_csv(file_bytes, account, filename=None, importer=None):
    return BankOfAmericaCreditCard().run(file_bytes, account, filename, importer)


# ── Checking / Savings ────────────────────────────────────────────────────────

@dataclass
class _CheckingCtx:
    balance: Optional[float] = None
    latest: Optional[date_type] = None


class BankOfAmericaChecking(CsvImporter[_CheckingCtx]):
    """BofA checking/savings export: 5-line summary preamble + blank line before
    the real transaction header. Amount is already signed (debit negative, credit
    positive). Running balance column → authoritative snapshot."""

    def skip_lines(self) -> int:
        return 6  # 5 summary lines + 1 blank line

    def row_start(self) -> int:
        return 8  # preamble(6) + header(1) + data starts at line 8

    def new_context(self) -> _CheckingCtx:
        return _CheckingCtx()

    def parse_row(self, row: dict, account: Account, ctx: _CheckingCtx) -> Optional[TransactionCreate]:
        raw = row["Amount"].strip().replace(",", "")
        if not raw:
            return None  # "Beginning balance" marker row — no amount, skip silently

        tx_date = datetime.strptime(row["Date"].strip(), "%m/%d/%Y").date()
        amount = round(float(raw), 2)

        if ctx.latest is None or tx_date >= ctx.latest:
            ctx.latest = tx_date
            ctx.balance = float(row["Running Bal."].strip().replace(",", ""))

        return TransactionCreate(
            date=tx_date,
            description=row["Description"].strip(),
            amount=amount,
            category="Uncategorized",
            account_id=account.id,
        )

    def balance_snapshot(self, ctx: _CheckingCtx) -> Optional[BalanceSnapshot]:
        if ctx.balance is None or ctx.latest is None:
            return None
        return BalanceSnapshot(amount=ctx.balance, as_of=ctx.latest)


def import_checking_savings_csv(file_bytes, account, filename=None, importer=None):
    return BankOfAmericaChecking().run(file_bytes, account, filename, importer)
