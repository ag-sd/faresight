from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.importers import IMPORTERS
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


# ── Import ────────────────────────────────────────────────────────────────────

@router.get("/importers")
def list_importers() -> list[str]:
    return list(IMPORTERS.keys())


@router.post("/transactions/import-bulk")
async def import_bulk(
    files: List[UploadFile] = File(...),
    account_id: int = Form(...),
    importer: str = Form(...),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if importer not in IMPORTERS:
        raise HTTPException(status_code=400, detail=f"Unknown importer: {importer!r}")

    import_fn = IMPORTERS[importer]
    results = []

    for file in files:
        file_bytes = await file.read()
        result = import_fn(file_bytes, account)
        for tx in result.transactions:
            db.add(Transaction(**tx.model_dump()))
        results.append({
            "filename": file.filename,
            "imported": len(result.transactions),
            "errors": result.errors,
        })

    db.commit()
    return results


