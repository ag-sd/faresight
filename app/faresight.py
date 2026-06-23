from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Optional
import asyncio

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.models import Transaction
from app.schemas import TransactionCreate, TransactionOut, TransactionUpdate
import app.sync as sync_mod


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    sync_mod.sync_from_nas()
    task = asyncio.create_task(sync_mod._periodic_sync_loop())
    yield
    # Graceful shutdown: cancel loop, push final copy, release lock.
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    sync_mod.sync_to_nas()
    sync_mod._release_lock()


app = FastAPI(title="Faresight — Expense Tracker", lifespan=lifespan)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=FileResponse)
def root():
    return FileResponse(FRONTEND_DIR / "app" / "pages" / "index.html")


# ── Transactions CRUD ─────────────────────────────────────────────────────────

@app.get("/api/transactions", response_model=list[TransactionOut])
def list_transactions(
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if category:
        q = q.filter(Transaction.category == category)
    return q.order_by(Transaction.date.desc()).all()


@app.post("/api/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(body: TransactionCreate, db: Session = Depends(get_db)):
    tx = Transaction(**body.model_dump())
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


@app.get("/api/transactions/{tx_id}", response_model=TransactionOut)
def get_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return tx


@app.patch("/api/transactions/{tx_id}", response_model=TransactionOut)
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


@app.delete("/api/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete(tx)
    db.commit()


# ── Summary / chart endpoints ─────────────────────────────────────────────────

@app.get("/api/summary/by-category")
def summary_by_category(db: Session = Depends(get_db)):
    rows = (
        db.query(Transaction.category, func.sum(Transaction.amount).label("total"))
        .group_by(Transaction.category)
        .all()
    )
    return [{"category": r.category, "total": round(r.total, 2)} for r in rows]


@app.get("/api/summary/by-month")
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


@app.get("/api/categories")
def list_categories(db: Session = Depends(get_db)):
    rows = db.query(Transaction.category).distinct().all()
    return sorted(r.category for r in rows)


# ── Sync endpoints ────────────────────────────────────────────────────────────

@app.get("/api/sync/status")
def sync_status():
    return sync_mod.get_status()


@app.post("/api/sync")
def push_sync():
    """Push local DB to NAS immediately. Also used for 'Proceed anyway' on lock conflict."""
    sync_mod.sync_to_nas()
    return sync_mod.get_status()


@app.post("/api/sync/go-offline")
def go_offline():
    """Disable NAS sync for this session ('Work offline' on lock conflict)."""
    sync_mod.disable_sync()
    return sync_mod.get_status()
