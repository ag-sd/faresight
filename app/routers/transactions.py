import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import case, extract, func
from sqlalchemy.orm import Session

from app.categorizer import PENDING_CONFIDENCE, _cat_status
from app.database import get_db
from app.importers import IMPORTERS
from app.models import Account, Transaction
from app.schemas import TransactionCreate, TransactionOut, TransactionUpdate

router = APIRouter(prefix="/api", tags=["transactions"])

logger = logging.getLogger(__name__)


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


@router.get("/summary/by-model-category")
def summary_by_model_category(db: Session = Depends(get_db)):
    rows = (
        db.query(Transaction.model_category, func.sum(Transaction.amount).label("total"))
        .filter(
            Transaction.model_category.isnot(None),
            Transaction.model_confidence != PENDING_CONFIDENCE,
        )
        .group_by(Transaction.model_category)
        .all()
    )
    return [{"category": r.model_category, "total": round(r.total, 2)} for r in rows]


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


# ── Categorizer status ────────────────────────────────────────────────────────

@router.get("/categorizer/status")
def categorizer_status(db: Session = Depends(get_db)):
    row = db.query(
        func.sum(case(
            (Transaction.model_confidence == PENDING_CONFIDENCE, 1), else_=0
        )).label("pending"),
        func.sum(case(
            (
                Transaction.model_confidence.isnot(None) &
                (Transaction.model_confidence != PENDING_CONFIDENCE),
                1,
            ),
            else_=0,
        )).label("categorized"),
    ).one()
    return {
        "pending": int(row.pending or 0),
        "categorized": int(row.categorized or 0),
        "throughput_ema": _cat_status["throughput_ema"],
    }


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

    parsed = []
    for file in files:
        file_bytes = await file.read()
        result = import_fn(file_bytes, account)
        parsed.append((file.filename, result))

    for filename, result in parsed:
        for tx in result.transactions:
            tx.model_confidence = PENDING_CONFIDENCE
            db.add(Transaction(**tx.model_dump()))
        results.append({
            "filename": filename,
            "imported": len(result.transactions),
            "errors": result.errors,
        })

    db.commit()
    return results


