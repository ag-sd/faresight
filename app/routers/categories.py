from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category
from app.schemas import CategoryCreate, CategoryOut, CategoryUpdate, VALID_BUCKETS

router = APIRouter(prefix="/api", tags=["categories"])


@router.get("/categories", response_model=List[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return db.query(Category).order_by(Category.sort_order, Category.name).all()


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryCreate, db: Session = Depends(get_db)):
    if body.bucket not in VALID_BUCKETS:
        raise HTTPException(status_code=422, detail=f"bucket must be one of {sorted(VALID_BUCKETS)}")
    if db.query(Category).filter(Category.name == body.name).first():
        raise HTTPException(status_code=409, detail=f"Category {body.name!r} already exists")
    max_order = db.query(func.max(Category.sort_order)).scalar() or 0
    cat = Category(
        name=body.name,
        color=body.color,
        bucket=body.bucket,
        description=body.description,
        sort_order=max_order + 1,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@router.patch("/categories/{name}", response_model=CategoryOut)
def update_category(name: str, body: CategoryUpdate, db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.name == name).first()
    if not cat:
        raise HTTPException(status_code=404, detail=f"Category {name!r} not found")
    if body.bucket is not None and body.bucket not in VALID_BUCKETS:
        raise HTTPException(status_code=422, detail=f"bucket must be one of {sorted(VALID_BUCKETS)}")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(cat, field, value)
    db.commit()
    db.refresh(cat)
    return cat


@router.delete("/categories/{name}", status_code=204)
def delete_category(name: str, db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.name == name).first()
    if not cat:
        raise HTTPException(status_code=404, detail=f"Category {name!r} not found")
    db.delete(cat)
    db.commit()
