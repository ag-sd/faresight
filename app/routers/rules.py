import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Rule, Transaction
from app.rule_matching import compile_rule
from app.schemas import RuleCreate, RuleOut, RuleUpdate

router = APIRouter(prefix="/api/rules", tags=["rules"])

# Max ids per UPDATE ... IN (...) chunk in apply_rule; safely under the 999
# bound-parameter limit of older SQLite builds.
_APPLY_CHUNK_SIZE = 500


def _validate_regex(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise HTTPException(status_code=422, detail=f"Invalid regular expression: {exc}")


@router.get("", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)):
    return db.query(Rule).order_by(Rule.created_at.desc()).all()


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(body: RuleCreate, db: Session = Depends(get_db)):
    if not db.query(Category).filter(Category.name == body.category).first():
        raise HTTPException(status_code=422, detail=f"Unknown category: {body.category!r}")
    _validate_regex(body.description)
    rule = Rule(**body.model_dump())
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule for this pattern and category already exists.",
        )
    db.refresh(rule)
    return rule


@router.patch("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    data = body.model_dump(exclude_unset=True)
    if "category" in data and not db.query(Category).filter(Category.name == data["category"]).first():
        raise HTTPException(status_code=422, detail=f"Unknown category: {data['category']!r}")
    if "description" in data:
        _validate_regex(data["description"])
    for field, value in data.items():
        setattr(rule, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule for this pattern and category already exists.",
        )
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()


@router.post("/{rule_id}/apply")
def apply_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    # SQLite has no native regex; filter candidates in Python, update by id.
    pattern = compile_rule(rule.description)
    candidates = (
        db.query(Transaction.id, Transaction.description)
        .filter(Transaction.user_modified_category == False)
        .all()
    )
    matched_ids = [tid for tid, desc in candidates if pattern.search(desc)]
    if not matched_ids:
        return {"updated": 0}

    # Chunk the IN list to stay under SQLite's bound-parameter limit
    # (999 on older builds). One commit covers all chunks.
    updated = 0
    for start in range(0, len(matched_ids), _APPLY_CHUNK_SIZE):
        chunk = matched_ids[start : start + _APPLY_CHUNK_SIZE]
        result = db.execute(
            update(Transaction)
            .where(Transaction.id.in_(chunk))
            .values(model_category=rule.category, model_confidence=10)
        )
        updated += result.rowcount
    db.commit()
    return {"updated": updated}
