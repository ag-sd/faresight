from datetime import date as date_type
from typing import Optional
import csv
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Transaction
from app.schemas import TransactionCreate, TransactionOut, TransactionUpdate

router = APIRouter(prefix="/api", tags=["transactions"])


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if category:
        q = q.filter(Transaction.category == category)
    return q.order_by(Transaction.date.desc()).all()


@router.post("/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(body: TransactionCreate, db: Session = Depends(get_db)):
    tx = Transaction(**body.model_dump())
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


@router.get("/transactions/{tx_id}", response_model=TransactionOut)
def get_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return tx


@router.patch("/transactions/{tx_id}", response_model=TransactionOut)
def update_transaction(
    tx_id: int, body: TransactionUpdate, db: Session = Depends(get_db)
):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tx, field, value)
    db.commit()
    db.refresh(tx)
    return tx


@router.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete(tx)
    db.commit()


# ── Summary / charts ──────────────────────────────────────────────────────────

@router.get("/summary/by-category")
def summary_by_category(db: Session = Depends(get_db)):
    rows = (
        db.query(Transaction.category, func.sum(Transaction.amount).label("total"))
        .group_by(Transaction.category)
        .all()
    )
    return [{"category": r.category, "total": round(r.total, 2)} for r in rows]


@router.get("/summary/by-month")
def summary_by_month(db: Session = Depends(get_db)):
    rows = (
        db.query(
            extract("year", Transaction.date).label("year"),
            extract("month", Transaction.date).label("month"),
            func.sum(Transaction.amount).label("total"),
        )
        .group_by("year", "month")
        .order_by("year", "month")
        .all()
    )
    return [
        {"year": int(r.year), "month": int(r.month), "total": round(r.total, 2)}
        for r in rows
    ]


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    rows = db.query(Transaction.category).distinct().all()
    return sorted(r.category for r in rows)


# ── Import ────────────────────────────────────────────────────────────────────

@router.post("/transactions/import")
async def import_csv(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    content = await file.read()
    text = content.decode("utf-8-sig")  # strip BOM if present
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    errors = []

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        try:
            tx = Transaction(
                date=date_type.fromisoformat(row["date"].strip()),
                description=row["description"].strip(),
                amount=float(row["amount"].strip()),
                category=row.get("category", "").strip() or "Uncategorized",
                note=row.get("note", "").strip() or None,
                account_id=account.id,
            )
            db.add(tx)
            imported += 1
        except KeyError as e:
            errors.append(f"Row {i}: missing column {e}")
        except ValueError as e:
            errors.append(f"Row {i}: {e}")

    db.commit()
    return {"imported": imported, "errors": errors}
