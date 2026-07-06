from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.categorizer import ALLOWED_CATEGORIES
from app.database import get_db
from app.importers import IMPORTERS
from app.models import FileImport, Rule, Transaction
from app.schemas import RuleCreate, RuleOut

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(importer: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Rule)
    if importer:
        q = q.filter(Rule.importer == importer)
    return q.order_by(Rule.created_at.desc()).all()


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(body: RuleCreate, db: Session = Depends(get_db)):
    if body.category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"Unknown category: {body.category!r}")
    if body.importer not in IMPORTERS:
        raise HTTPException(status_code=422, detail=f"Unknown importer: {body.importer!r}")
    rule = Rule(**body.model_dump())
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule for this exact description, category, and importer already exists.",
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

    file_import_ids = [
        row.id
        for row in db.query(FileImport.id).filter(FileImport.importer == rule.importer).all()
    ]

    if not file_import_ids:
        return {"updated": 0}

    result = db.execute(
        update(Transaction)
        .where(
            Transaction.file_id.in_(file_import_ids),
            Transaction.description == rule.description,
            Transaction.user_modified_category == False,
        )
        .values(model_category=rule.category, model_confidence=10)
    )
    db.commit()
    return {"updated": result.rowcount}
