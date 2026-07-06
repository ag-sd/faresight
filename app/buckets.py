"""Category-bucket helpers shared by the summary and insights routers.

Bucket membership is a string match of Transaction.model_category against
categories.name (no FK by design — see CLAUDE.md).
"""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Category, Transaction


def bucket_names(db: Session, bucket: str) -> list[str]:
    """Category names belonging to a bucket (income | spend | internal)."""
    return [r.name for r in db.query(Category.name).filter(Category.bucket == bucket).all()]


def bucket_filter(q, db: Session, bucket: str):
    """Restrict a transactions query to one bucket via model_category.

    income — strict: uncategorized rows are never income.
    spend  — NULL-safe: uncategorized rows count as spend, consistent with the
             existing charts that keep them in spending aggregates.
    """
    if bucket == "income":
        return q.filter(Transaction.model_category.in_(bucket_names(db, "income")))
    return q.filter(
        or_(
            Transaction.model_category.is_(None),
            Transaction.model_category.in_(bucket_names(db, "spend")),
        )
    )
