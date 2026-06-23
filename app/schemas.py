from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


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
