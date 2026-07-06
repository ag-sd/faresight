"""Insight endpoints: recurring-charge detection, category trends (MoM movers +
trailing averages), and top merchants.

Conventions shared with the summary endpoints: raw signed sums (spend negative),
bucket membership via model_category string match (app/buckets.py), optional
date params defaulting to today so tests stay deterministic.
"""
from collections import defaultdict
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import extract, func, or_
from sqlalchemy.orm import Session

from app.buckets import bucket_filter, bucket_names
from app.database import get_db
from app.insights import MONTHLY_FACTOR, detect_recurring
from app.models import Transaction
from app.schemas import CategoryTrend, MerchantSummary, RecurringOut

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/recurring", response_model=RecurringOut)
def recurring(as_of: Optional[date] = None, db: Session = Depends(get_db)):
    as_of = as_of or date.today()

    # Candidates: charges only; internal excluded NULL-safely (card payments
    # recur but aren't subscriptions; uncategorized rows stay — a brand-new
    # subscription may not be categorized yet).
    internal = bucket_names(db, "internal")
    q = db.query(Transaction).filter(Transaction.amount < 0)
    if internal:
        q = q.filter(
            or_(
                Transaction.model_category.is_(None),
                Transaction.model_category.notin_(internal),
            )
        )

    groups: dict[tuple, list] = defaultdict(list)
    for tx in q.all():
        groups[(tx.account_id, tx.description)].append((tx.date, tx.amount))

    items = []
    for (account_id, description), rows in groups.items():
        hit = detect_recurring([d for d, _ in rows], [a for _, a in rows], as_of)
        if hit:
            items.append({"description": description, "account_id": account_id, **hit})

    items.sort(key=lambda i: i["next_expected"])
    monthly_total = sum(i["amount"] * MONTHLY_FACTOR[i["cadence"]] for i in items)
    return {"items": items, "monthly_total": round(monthly_total, 2)}


def _month_category_sums(db: Session, year: int, month: int, spend_names: list[str]) -> dict[str, float]:
    rows = (
        db.query(Transaction.model_category, func.sum(Transaction.amount).label("total"))
        .filter(
            Transaction.model_category.in_(spend_names),
            extract("year", Transaction.date) == year,
            extract("month", Transaction.date) == month,
        )
        .group_by(Transaction.model_category)
        .all()
    )
    return {r.model_category: round(r.total, 2) for r in rows}


def _shift_month(year: int, month: int, back: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) - back
    return idx // 12, idx % 12 + 1


@router.get("/category-trends", response_model=List[CategoryTrend])
def category_trends(
    year: Optional[int] = None,
    month: Optional[int] = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    today = date.today()
    year = year if year is not None else today.year
    month = month if month is not None else today.month
    if not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail=f"Invalid month: {month}")

    # Strict spend-category membership: uncategorized rows are skipped here —
    # early-import noise would dominate the movers list. The cashflow/spend
    # totals elsewhere still count them.
    spend_names = bucket_names(db, "spend")
    if not spend_names:
        return []

    current = _month_category_sums(db, year, month, spend_names)
    prev_y, prev_m = _shift_month(year, month, 1)
    previous = _month_category_sums(db, prev_y, prev_m, spend_names)

    # Trailing 3-month average: sum over the 3 calendar months preceding the
    # selected month, divided by how many of those months hold any transaction
    # at all (young-DB safety) — None when none do.
    window = [_shift_month(year, month, b) for b in (1, 2, 3)]
    window_sums: dict[str, float] = defaultdict(float)
    covered = 0
    for w_y, w_m in window:
        month_any = (
            db.query(func.count(Transaction.id))
            .filter(
                extract("year", Transaction.date) == w_y,
                extract("month", Transaction.date) == w_m,
            )
            .scalar()
        )
        if month_any:
            covered += 1
        for cat, total in _month_category_sums(db, w_y, w_m, spend_names).items():
            window_sums[cat] += total

    trends = []
    for cat in set(current) | set(previous):
        cur = current.get(cat, 0.0)
        prev = previous.get(cat, 0.0)
        if cur == 0.0 and prev == 0.0:
            continue
        trends.append({
            "category": cat,
            "current": cur,
            "previous": prev,
            "delta": round(cur - prev, 2),
            "avg_3mo": round(window_sums.get(cat, 0.0) / covered, 2) if covered else None,
        })

    trends.sort(key=lambda t: abs(t["delta"]), reverse=True)
    return trends[:limit]


@router.get("/top-merchants", response_model=List[MerchantSummary])
def top_merchants(
    year: Optional[int] = None,
    month: Optional[int] = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    today = date.today()
    year = year if year is not None else today.year
    if month is not None and not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail=f"Invalid month: {month}")

    q = db.query(
        Transaction.description,
        func.sum(Transaction.amount).label("total"),
        func.count(Transaction.id).label("count"),
    ).filter(extract("year", Transaction.date) == year)
    if month is not None:
        q = q.filter(extract("month", Transaction.date) == month)
    q = bucket_filter(q, db, "spend")

    rows = (
        q.group_by(Transaction.description)
        .order_by(func.sum(Transaction.amount))  # most negative = most spend first
        .limit(limit)
        .all()
    )
    return [
        {"description": r.description, "total": round(r.total, 2), "count": r.count}
        for r in rows
    ]
