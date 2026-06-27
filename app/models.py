import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.schemas import TransactionCreate

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AccountType(str, enum.Enum):
    credit_card = "credit_card"
    checking = "checking"
    savings = "savings"


class SourceFrequency(str, enum.Enum):
    weekly = "weekly"
    monthly = "monthly"
    yearly = "yearly"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=True)
    # AI-suggested category for human review; never overwrites `category`.
    model_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bank: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_number: Mapped[str] = mapped_column(String(50), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(Enum(AccountType), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    source_account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )
    source_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_frequency: Mapped[Optional[SourceFrequency]] = mapped_column(
        Enum(SourceFrequency), nullable=True
    )


@dataclass
class ImportResult:
    transactions: list["TransactionCreate"] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
