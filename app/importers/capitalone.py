import csv
import io
from datetime import date as date_type

from app.models import Account, ImportResult
from app.schemas import TransactionCreate


def import_credit_card_csv(file_bytes: bytes, account: Account) -> ImportResult:
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    transactions, errors = [], []

    for i, row in enumerate(reader, start=2):
        try:
            tx_date = date_type.fromisoformat(row["Transaction Date"].strip())
            description = row["Description"].strip()
            category = row["Category"].strip() or "Uncategorized"

            debit = row["Debit"].strip()
            credit = row["Credit"].strip()
            if debit:
                amount = -float(debit)
            elif credit:
                amount = float(credit)
            else:
                raise ValueError("row has neither Debit nor Credit")

            transactions.append(TransactionCreate(
                date=tx_date,
                description=description,
                amount=round(amount, 2),
                category=category,
                account_id=account.id,
            ))
        except (KeyError, ValueError) as e:
            errors.append(f"Row {i}: {e}")

    return ImportResult(transactions=transactions, errors=errors)
