import csv
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import case, extract, func, or_
from sqlalchemy.orm import Session

from app.config import PAGE_SIZE
from app.database import get_db
from app.importers import IMPORTERS
from app.models import Account, AccountType, FileImport, Rule, Transaction
from app.schemas import FileImportOut, PaginatedFileImports, PaginatedTransactions, TransactionCreate, TransactionCreateWithFile, TransactionOut, TransactionUpdate

router = APIRouter(prefix="/api", tags=["transactions"])

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

# Subset of app.categorizer.ALLOWED_CATEGORIES — money movement, not spending.
# "Transfers & Fees" was split into Transfers/Fees/Interest Income/Interest Paid:
# only Transfers is money movement and excluded here. Fees and Interest Paid are
# real spending, and Interest Income is real income — all three are counted.
# This tuple is the single point to update.
TRANSFER_CATEGORIES = ("Payments", "Transfers")


def _filter_by_account_type(q, account_type: Optional[str]):
    """Join accounts and filter by type. 'bank' matches checking + savings."""
    if not account_type or account_type == "all":
        return q
    q = q.join(Account, Transaction.account_id == Account.id)
    if account_type == "credit_card":
        return q.filter(Account.account_type == AccountType.credit_card)
    return q.filter(Account.account_type.in_([AccountType.checking, AccountType.savings]))


def _exclude_transfers(q):
    """Exclude payment/transfer rows from spending aggregates.

    NULL-safe: SQL NOT IN drops NULL rows, so keep model_category IS NULL rows.
    """
    return q.filter(
        or_(
            Transaction.model_category.is_(None),
            Transaction.model_category.notin_(TRANSFER_CATEGORIES),
        )
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/transactions", response_model=PaginatedTransactions)
def list_transactions(
    category: Optional[str] = None,
    account_type: Optional[str] = None,
    pending_only: bool = False,
    page: int = 1,
    limit: int = PAGE_SIZE,
    db: Session = Depends(get_db),
):
    offset = (page - 1) * limit
    q = db.query(Transaction)
    q = _filter_by_account_type(q, account_type)
    if category:
        q = q.filter(Transaction.category == category)
    if pending_only:
        q = q.filter(Transaction.model_confidence == -1)
    total = q.count()
    data = q.order_by(Transaction.date.desc()).offset(offset).limit(limit).all()
    return {"data": data, "limit": limit, "offset": offset, "total": total}


@router.post("/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(body: TransactionCreateWithFile, db: Session = Depends(get_db)):
    if not db.get(FileImport, body.file_id):
        raise HTTPException(status_code=422, detail=f"FileImport {body.file_id} does not exist")
    if body.account_id is not None and not db.get(Account, body.account_id):
        raise HTTPException(status_code=422, detail=f"Account {body.account_id} does not exist")
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
def summary_by_category(account_type: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Transaction.category, func.sum(Transaction.amount).label("total"))
    q = _filter_by_account_type(q, account_type)
    q = _exclude_transfers(q)
    rows = q.group_by(Transaction.category).all()
    return [{"category": r.category, "total": round(r.total, 2)} for r in rows]


@router.get("/summary/by-model-category")
def summary_by_model_category(db: Session = Depends(get_db)):
    rows = (
        db.query(Transaction.model_category, func.sum(Transaction.amount).label("total"))
        .filter(
            Transaction.model_category.isnot(None),
            Transaction.model_confidence != -1,
        )
        .group_by(Transaction.model_category)
        .all()
    )
    return [{"category": r.model_category, "total": round(r.total, 2)} for r in rows]


@router.get("/summary/by-month")
def summary_by_month(account_type: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(
        extract("year", Transaction.date).label("year"),
        extract("month", Transaction.date).label("month"),
        func.sum(Transaction.amount).label("total"),
    )
    q = _filter_by_account_type(q, account_type)
    q = _exclude_transfers(q)
    rows = q.group_by("year", "month").order_by("year", "month").all()
    return [
        {"year": int(r.year), "month": int(r.month), "total": round(r.total, 2)}
        for r in rows
    ]


@router.get("/summary/by-category-for-period")
def summary_by_category_for_period(
    year: int,
    month: Optional[int] = None,
    account_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = (
        db.query(Transaction.model_category, func.sum(Transaction.amount).label("total"))
        .filter(
            Transaction.model_category.isnot(None),
            Transaction.model_confidence != -1,
            extract("year", Transaction.date) == year,
        )
    )
    q = _filter_by_account_type(q, account_type)
    q = _exclude_transfers(q)
    if month is not None:
        q = q.filter(extract("month", Transaction.date) == month)
    rows = q.group_by(Transaction.model_category).all()
    return [{"category": r.model_category, "total": round(r.total, 2)} for r in rows]


# ── Categorizer status ────────────────────────────────────────────────────────

@router.get("/categorizer/status")
def categorizer_status(db: Session = Depends(get_db)):
    row = db.query(
        func.sum(case(
            (Transaction.model_confidence == -1, 1), else_=0
        )).label("pending"),
        func.sum(case(
            (
                Transaction.model_confidence.isnot(None) &
                (Transaction.model_confidence != -1),
                1,
            ),
            else_=0,
        )).label("categorized"),
    ).one()
    return {
        "pending": int(row.pending or 0),
        "categorized": int(row.categorized or 0),
    }


@router.get("/categorizer/running")
def categorizer_running(request: Request):
    proc = getattr(request.app.state, "cat_proc", None)
    return {"running": proc is not None and proc.poll() is None}


# ── Import ────────────────────────────────────────────────────────────────────

@router.get("/file-imports", response_model=PaginatedFileImports)
def list_file_imports(page: int = 1, limit: int = PAGE_SIZE, db: Session = Depends(get_db)):
    offset = (page - 1) * limit
    total = db.query(func.count(FileImport.id)).scalar() or 0
    data = db.query(FileImport).order_by(FileImport.id.desc()).offset(offset).limit(limit).all()
    return {"data": data, "limit": limit, "offset": offset, "total": total}


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
        filename = file.filename
        file_bytes = await file.read()
        try:
            result = import_fn(file_bytes, account, filename, importer)
        except (UnicodeDecodeError, csv.Error) as exc:
            logger.warning("Parse failed for %r: %s", filename, exc)
            db.add(FileImport(filename=filename, rows_seen=0, rows_persisted=0, account_id=account_id, importer=importer))
            results.append({"filename": filename, "imported": 0, "errors": [f"Could not parse file: {exc}"]})
            continue
        except Exception as exc:
            logger.error("Unexpected error importing %r: %s", filename, exc, exc_info=True)
            db.add(FileImport(filename=filename, rows_seen=0, rows_persisted=0, account_id=account_id, importer=importer))
            results.append({"filename": filename, "imported": 0, "errors": [f"Unexpected error: {exc}"]})
            continue

        rows_seen = len(result.transactions) + len(result.errors)
        log = FileImport(
            filename=filename, rows_seen=rows_seen, rows_persisted=0,
            account_id=account_id, importer=importer,
        )
        db.add(log)
        db.flush()  # populate log.id before inserting transactions

        # Pre-classify transactions that match a rule for this importer.
        rule_map = {
            r.description: r.category
            for r in db.query(Rule).filter(Rule.importer == importer).all()
        }
        for tx in result.transactions:
            if tx.description in rule_map:
                tx.model_category = rule_map[tx.description]
                tx.model_confidence = 10

        imported = 0
        for tx in result.transactions:
            d = tx.model_dump()
            d["file_id"] = log.id
            db.add(Transaction(**d))
            imported += 1

        log.rows_persisted = imported

        # Authoritative snapshots are idempotent (set-to-latest), so we apply
        # them directly. result.net_delta is intentionally NOT written to
        # current_balance yet — delta accumulation needs a re-import guard first.
        if result.snapshot is not None:
            account.current_balance = result.snapshot.amount

        results.append({"filename": filename, "imported": imported, "errors": result.errors})

    db.commit()
    return results


