from datetime import datetime

from app.importers.base import CsvImporter
from app.models import Account
from app.schemas import TransactionCreate


class ChaseCreditCard(CsvImporter[dict]):
    """Chase credit-card export: a Transaction Date (m/d/Y), a Description,
    a bank-provided Category we ignore, a Type (Sale/Payment/Return), and an
    already-signed Amount (charge negative, payment/return positive — matching
    our invariant, so no sign flip). No Reference Number or balance column."""

    def parse_row(self, row: dict, account: Account, ctx: dict) -> TransactionCreate:
        tx_date = datetime.strptime(row["Transaction Date"].strip(), "%m/%d/%Y").date()
        description = row["Description"].strip()
        amount = round(float(row["Amount"].strip()), 2)
        bank_category = row["Category"].strip() or "Uncategorized"

        return TransactionCreate(
            date=tx_date,
            description=description,
            amount=amount,
            bank_category=bank_category,
            account_id=account.id,
            reference_number=None,
        )


# ── Registry-facing wrapper ───────────────────────────────────────────────────

def import_credit_card_csv(file_bytes, account, filename=None, importer=None):
    return ChaseCreditCard().run(file_bytes, account, filename, importer)
