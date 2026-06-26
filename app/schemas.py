from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from app.models import AccountType, SourceFrequency


class TransactionCreate(BaseModel):
    date: date
    description: str
    amount: float
    category: str
    note: Optional[str] = None
    source: Optional[str] = None


class TransactionUpdate(BaseModel):
    date: Optional[date] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    note: Optional[str] = None
    source: Optional[str] = None


class TransactionOut(TransactionCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Accounts ──────────────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    bank: str
    name: str
    account_number: str
    account_type: AccountType
    notes: Optional[str] = None
    source_account_id: Optional[int] = None
    source_amount: Optional[float] = None
    source_frequency: Optional[SourceFrequency] = None


class AccountUpdate(BaseModel):
    bank: Optional[str] = None
    name: Optional[str] = None
    account_number: Optional[str] = None
    account_type: Optional[AccountType] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    source_account_id: Optional[int] = None
    source_amount: Optional[float] = None
    source_frequency: Optional[SourceFrequency] = None


class AccountOut(AccountCreate):
    id: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
