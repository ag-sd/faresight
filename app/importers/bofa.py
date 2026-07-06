from datetime import datetime

from app.importers.base import CsvImporter
from app.models import Account
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
