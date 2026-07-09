from dataclasses import dataclass
from datetime import date as date_type
from typing import Optional

from app.importers.base import CsvImporter
from app.models import Account, BalanceSnapshot
from app.schemas import TransactionCreate


@dataclass
class _SoFiCtx:
    balance: Optional[float] = None
    latest: Optional[date_type] = None


class SoFiChecking(CsvImporter[_SoFiCtx]):
    """SoFi checking/savings export: ISO dates (YYYY-MM-DD), pre-signed Amount
    (withdrawals already negative), a Current balance column for the snapshot.
    No preamble, no reference number."""

    def new_context(self) -> _SoFiCtx:
        return _SoFiCtx()

    def parse_row(self, row: dict, account: Account, ctx: _SoFiCtx) -> TransactionCreate:
        tx_date = date_type.fromisoformat(row["Date"].strip())
        description = row["Description"].strip()
        amount = round(float(row["Amount"].strip()), 2)
        bank_category = row["Type"].strip() or "Uncategorized"

        if ctx.latest is None or tx_date >= ctx.latest:
            ctx.latest = tx_date
            ctx.balance = float(row["Current balance"].strip())

        return TransactionCreate(
            date=tx_date,
            description=description,
            amount=amount,
            bank_category=bank_category,
            account_id=account.id,
        )

    def balance_snapshot(self, ctx: _SoFiCtx) -> Optional[BalanceSnapshot]:
        if ctx.balance is None or ctx.latest is None:
            return None
        return BalanceSnapshot(amount=ctx.balance, as_of=ctx.latest)


# ── Registry-facing wrapper ───────────────────────────────────────────────────

def import_checking_savings_csv(file_bytes, account, filename=None, importer=None):
    return SoFiChecking().run(file_bytes, account, filename, importer)
