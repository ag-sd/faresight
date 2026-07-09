import csv
import hashlib
import logging
from collections import Counter
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import case, extract, func, or_
from sqlalchemy.orm import Session

from app.buckets import bucket_filter, bucket_names
from app.config import PAGE_SIZE
from app.database import get_db
from app.importers import IMPORTERS
from app.models import Account, AccountType, BalanceHistory, Category, FileImport, Rule, Transaction, dedup_hash_for
from app.schemas import BadgeSummary, CashFlowPoint, CategorySummary, FileImportOut, MonthlySummary, PaginatedFileImports, PaginatedTransactions, TransactionCreate, TransactionCreateWithFile, TransactionOut, TransactionUpdate

router = APIRouter(prefix="/api", tags=["transactions"])

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _filter_by_account_type(q, account_type: Optional[str]):
    """Join accounts and filter by type. 'bank' matches checking + savings."""
    if not account_type or account_type == "all":
        return q
    q = q.join(Account, Transaction.account_id == Account.id)
    if account_type == "credit_card":
        return q.filter(Account.account_type == AccountType.credit_card)
    return q.filter(Account.account_type.in_([AccountType.checking, AccountType.savings]))


def _dedupe_rows(db: Session, txs) -> tuple[list, int]:
    """Occurrence-counting re-import guard. For each identity hash, accept only
    the copies the DB does not already hold — legitimate duplicates within one
    file (two identical bus fares) all import the first time, while re-imports
    and overlapping exports contribute nothing. Returns ([(tx, hash), ...] to
    insert, skipped count)."""
    hashes = [
        dedup_hash_for(tx.account_id, tx.date, tx.description, tx.amount, getattr(tx, "reference_number", None))
        for tx in txs
    ]
    existing = dict(
        db.query(Transaction.dedup_hash, func.count(Transaction.id))
        .filter(Transaction.dedup_hash.in_(set(hashes)))
        .group_by(Transaction.dedup_hash)
        .all()
    )
    seen: Counter = Counter()
    to_insert, skipped = [], 0
    for tx, h in zip(txs, hashes):
        seen[h] += 1
        if seen[h] > existing.get(h, 0):
            to_insert.append((tx, h))
        else:
            skipped += 1
    return to_insert, skipped


def _log_balance(db: Session, account_id: int, balance: float, as_of):
    """Append a point to balance_history — an append-only audit log of every
    balance an import stated or derived, enabling net-worth-over-time charting
    later. Stale snapshots (older as_of than what's recorded) are logged too;
    they are legitimate history even when they don't win current_balance."""
    db.add(BalanceHistory(account_id=account_id, balance=balance, as_of=as_of))


def _exclude_internal(q, db: Session):
    """Exclude bucket='internal' categories from spending aggregates.

    NULL-safe: SQL NOT IN silently drops NULL rows, so we explicitly keep
    model_category IS NULL rows (legacy / uncategorized transactions).
    """
    names = bucket_names(db, "internal")
    if not names:
        return q
    return q.filter(
        or_(
            Transaction.model_category.is_(None),
            Transaction.model_category.notin_(names),
        )
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/transactions", response_model=PaginatedTransactions)
def list_transactions(
    account_type: Optional[str] = None,
    pending_only: bool = False,
    page: int = 1,
    limit: int = PAGE_SIZE,
    db: Session = Depends(get_db),
):
    offset = (page - 1) * limit
    q = db.query(Transaction)
    q = _filter_by_account_type(q, account_type)
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
    data = body.model_dump()
    # A category supplied on manual create is a human choice: it becomes the
    # display category, pinned against the background categorizer. It wins over
    # any explicitly-posted model_* fields on purpose.
    category = data.pop("category")
    if category is not None:
        data["model_category"] = category
        data["model_confidence"] = 10
        data["user_modified_category"] = True
    # Manual rows carry the identity hash too, so a later CSV import containing
    # a hand-entered transaction dedupes against it instead of duplicating.
    data["dedup_hash"] = dedup_hash_for(body.account_id, body.date, body.description, body.amount, body.reference_number)
    tx = Transaction(**data)
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

@router.get("/summary/by-model-category", response_model=List[CategorySummary])
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


@router.get("/summary/by-month", response_model=List[MonthlySummary])
def summary_by_month(
    account_type: Optional[str] = None,
    bucket: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if bucket is not None and bucket not in ("income", "spend"):
        raise HTTPException(status_code=422, detail=f"Unknown bucket: {bucket!r}")
    q = db.query(
        extract("year", Transaction.date).label("year"),
        extract("month", Transaction.date).label("month"),
        func.sum(Transaction.amount).label("total"),
    )
    q = _filter_by_account_type(q, account_type)
    if bucket is not None:
        # Bucket membership implies internal exclusion.
        q = bucket_filter(q, db, bucket)
    else:
        q = _exclude_internal(q, db)
    rows = q.group_by("year", "month").order_by("year", "month").all()
    return [
        {"year": int(r.year), "month": int(r.month), "total": round(r.total, 2)}
        for r in rows
    ]


@router.get("/summary/by-category-for-period", response_model=List[CategorySummary])
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
    q = _exclude_internal(q, db)
    if month is not None:
        q = q.filter(extract("month", Transaction.date) == month)
    rows = q.group_by(Transaction.model_category).all()
    return [{"category": r.model_category, "total": round(r.total, 2)} for r in rows]


def _flow_sums(db: Session):
    """Conditional-sum expressions for income and spend, by bucket membership.

    income — strict (uncategorized is never income); spend — NULL-safe (uncategorized
    counts as spend). Internal drops out of both. Raw signed sums: income positive,
    spend negative.
    """
    income_names = bucket_names(db, "income")
    spend_names = bucket_names(db, "spend")
    income_sum = func.sum(
        case((Transaction.model_category.in_(income_names), Transaction.amount), else_=0.0)
    )
    spend_sum = func.sum(
        case(
            (
                or_(
                    Transaction.model_category.is_(None),
                    Transaction.model_category.in_(spend_names),
                ),
                Transaction.amount,
            ),
            else_=0.0,
        )
    )
    return income_sum, spend_sum


@router.get("/summary/cashflow", response_model=List[CashFlowPoint])
def summary_cashflow(db: Session = Depends(get_db)):
    income_sum, spend_sum = _flow_sums(db)
    rows = (
        db.query(
            extract("year", Transaction.date).label("year"),
            extract("month", Transaction.date).label("month"),
            income_sum.label("income"),
            spend_sum.label("spend"),
        )
        .group_by("year", "month")
        .order_by("year", "month")
        .all()
    )
    return [
        {
            "year": int(r.year),
            "month": int(r.month),
            "income": round(r.income, 2),
            "spend": round(r.spend, 2),
            "net": round(r.income + r.spend, 2),
        }
        for r in rows
    ]


def _month_flow(db: Session, year: int, month: int) -> tuple[float, float]:
    """(income, spend) raw signed sums for one calendar month."""
    income_sum, spend_sum = _flow_sums(db)
    row = (
        db.query(income_sum.label("income"), spend_sum.label("spend"))
        .filter(
            extract("year", Transaction.date) == year,
            extract("month", Transaction.date) == month,
        )
        .one()
    )
    return round(row.income or 0.0, 2), round(row.spend or 0.0, 2)


@router.get("/summary/badges", response_model=BadgeSummary)
def summary_badges(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
):
    today = date.today()
    year = year if year is not None else today.year
    month = month if month is not None else today.month
    if not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail=f"Invalid month: {month}")

    balances = [
        a.current_balance
        for a in db.query(Account).filter(Account.is_active == True).all()
        if a.current_balance is not None
    ]
    assets = round(sum(b for b in balances if b > 0), 2)
    liabilities = round(sum(b for b in balances if b < 0), 2)

    month_income, month_spend = _month_flow(db, year, month)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_income, prev_spend = _month_flow(db, prev_year, prev_month)

    return {
        "net_worth": round(assets + liabilities, 2),
        "assets": assets,
        "liabilities": liabilities,
        "month_income": month_income,
        "month_spend": month_spend,
        "prev_month_income": prev_income,
        "prev_month_spend": prev_spend,
        # spend is negative, so this is (income − |spend|) / income
        "savings_rate": round((month_income + month_spend) / month_income, 4)
        if month_income > 0 else None,
    }


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
        file_sha = hashlib.sha256(file_bytes).hexdigest()

        # Layer 1 — exact-file short-circuit. Flush first so files earlier in
        # this same request are visible (the session has autoflush off).
        # rows_persisted > 0 keeps failed imports retryable.
        db.flush()
        duplicate = (
            db.query(FileImport)
            .filter(
                FileImport.account_id == account_id,
                FileImport.content_hash == file_sha,
                FileImport.rows_persisted > 0,
            )
            .first()
        )
        if duplicate:
            results.append({
                "filename": filename, "imported": 0, "skipped": 0,
                "errors": [], "duplicate_file": True,
            })
            continue

        try:
            result = import_fn(file_bytes, account, filename, importer)
        except (UnicodeDecodeError, csv.Error) as exc:
            logger.warning("Parse failed for %r: %s", filename, exc)
            db.add(FileImport(filename=filename, rows_seen=0, rows_persisted=0, account_id=account_id, importer=importer, content_hash=file_sha))
            results.append({"filename": filename, "imported": 0, "skipped": 0, "errors": [f"Could not parse file: {exc}"]})
            continue
        except Exception as exc:
            logger.error("Unexpected error importing %r: %s", filename, exc, exc_info=True)
            db.add(FileImport(filename=filename, rows_seen=0, rows_persisted=0, account_id=account_id, importer=importer, content_hash=file_sha))
            results.append({"filename": filename, "imported": 0, "skipped": 0, "errors": [f"Unexpected error: {exc}"]})
            continue

        rows_seen = len(result.transactions) + len(result.errors)
        log = FileImport(
            filename=filename, rows_seen=rows_seen, rows_persisted=0,
            account_id=account_id, importer=importer, content_hash=file_sha,
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

        # Layer 2 — row-level occurrence-counting dedupe.
        to_insert, skipped = _dedupe_rows(db, result.transactions)
        inserted_delta = 0.0
        for tx, tx_hash in to_insert:
            d = tx.model_dump()
            d["file_id"] = log.id
            d["dedup_hash"] = tx_hash
            db.add(Transaction(**d))
            inserted_delta += tx.amount

        log.rows_persisted = len(to_insert)
        log.rows_skipped = skipped

        # Authoritative snapshots are idempotent (set-to-latest) and win when
        # present. Otherwise accumulate the delta of the rows actually inserted
        # — not result.net_delta, which ignores dedupe — so re-imports cannot
        # double-count. This is what gives snapshot-less accounts (credit
        # cards) a derived balance.
        if result.snapshot is not None:
            # Set-to-latest arbitration: only a snapshot at least as new as the
            # account's most recent recorded balance may move current_balance —
            # an out-of-order backfill of an older statement must not regress
            # it. Ties win, so re-uploading (or correcting) the same-dated
            # statement re-applies. Flush first: autoflush is off and earlier
            # files in this request may have logged history.
            db.flush()
            latest = (
                db.query(func.max(BalanceHistory.as_of))
                .filter(BalanceHistory.account_id == account_id)
                .scalar()
            )
            if latest is None or result.snapshot.as_of >= latest:
                account.current_balance = result.snapshot.amount
            _log_balance(db, account_id, result.snapshot.amount, result.snapshot.as_of)
        elif to_insert:
            account.current_balance = round((account.current_balance or 0.0) + inserted_delta, 2)
            _log_balance(db, account_id, account.current_balance, max(tx.date for tx, _ in to_insert))

        results.append({"filename": filename, "imported": len(to_insert), "skipped": skipped, "errors": result.errors})

    db.commit()
    return results


