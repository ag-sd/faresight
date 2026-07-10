from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import BANK_LOGOS
from app.database import get_db
from app.importers import IMPORTERS
from app.models import Account
from app.schemas import AccountCreate, AccountOut, AccountUpdate

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("/bank-logos")
def get_bank_logos():
    return BANK_LOGOS


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return db.query(Account).order_by(Account.id.desc()).all()


@router.post("", response_model=AccountOut, status_code=201)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    if body.default_importer not in IMPORTERS:
        raise HTTPException(status_code=422, detail=f"Unknown importer: {body.default_importer!r}")
    if body.source_account_id is not None:
        src = db.get(Account, body.source_account_id)
        if not src:
            raise HTTPException(status_code=422, detail="Source account not found")
        if not src.is_active:
            raise HTTPException(status_code=422, detail="Source account is not active")
    account = Account(**body.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, body: AccountUpdate, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    updates = body.model_dump(exclude_unset=True)
    if "default_importer" in updates and updates["default_importer"] not in IMPORTERS:
        raise HTTPException(status_code=422, detail=f"Unknown importer: {updates['default_importer']!r}")
    if updates.get("is_active") is False:
        linked = db.query(Account).filter(Account.source_account_id == account_id).first()
        if linked:
            raise HTTPException(
                status_code=422,
                detail=f'Cannot deactivate: "{linked.name}" is linked to this account as its source.',
            )
    if updates.get("source_account_id") is not None:
        src_id = updates["source_account_id"]
        if src_id == account_id:
            raise HTTPException(status_code=422, detail="An account cannot be its own source")
        src = db.get(Account, src_id)
        if not src:
            raise HTTPException(status_code=422, detail="Source account not found")
        if not src.is_active:
            raise HTTPException(status_code=422, detail="Source account is not active")
    for field, value in updates.items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    return account
