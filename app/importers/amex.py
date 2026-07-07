from datetime import date as date_type, datetime
from typing import Optional

from app.importers.base import CsvImporter
from app.models import Account
from app.schemas import TransactionCreate


class AmexSavings(CsvImporter[dict]):
    """Amex savings export: no header row, three columns (Date, Description,
    Amount), ISO dates, pre-signed amounts. No balance column, so no snapshot."""

    def fieldnames(self) -> list:
        return ["Date", "Description", "Amount"]

    def row_start(self) -> int:
        return 1  # line 1 is a data row, not a header

    def parse_row(self, row: dict, account: Account, ctx: dict) -> TransactionCreate:
        tx_date = date_type.fromisoformat(row["Date"].strip())
        amount = round(float(row["Amount"].strip()), 2)

        return TransactionCreate(
            date=tx_date,
            description=row["Description"].strip(),
            amount=amount,
            category="Uncategorized",
            account_id=account.id,
        )


class AmexCreditCard(CsvImporter[dict]):
    """Amex credit-card export: m/d/Y dates, sign-inverted Amount (purchases
    positive in the file → flip to negative), single-quote-wrapped Reference,
    and a Category column used as a hint."""

    def parse_row(self, row: dict, account: Account, ctx: dict) -> TransactionCreate:
        tx_date = datetime.strptime(row["Date"].strip(), "%m/%d/%Y").date()
        amount = round(-float(row["Amount"].strip()), 2)  # Amex sign is inverted
        ref = row["Reference"].strip().strip("'") or None
        category = row["Category"].strip() or "Uncategorized"

        return TransactionCreate(
            date=tx_date,
            description=row["Description"].strip(),
            amount=amount,
            category=category,
            account_id=account.id,
            reference_number=ref,
        )


# ── Registry-facing wrappers ──────────────────────────────────────────────────

def import_savings_csv(file_bytes, account, filename=None, importer=None):
    return AmexSavings().run(file_bytes, account, filename, importer)


def import_credit_card_csv(file_bytes, account, filename=None, importer=None):
    return AmexCreditCard().run(file_bytes, account, filename, importer)
