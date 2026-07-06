import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.schemas import TransactionCreate

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint, func
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
    account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=True)
    # AI-suggested category for human review; never overwrites `category`.
    model_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=-1)
    user_modified_category: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("file_imports.id"), nullable=False)
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
    current_balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class FileImport(Base):
    __tablename__ = "file_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_persisted: Mapped[int] = mapped_column(Integer, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=True)
    importer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class Rule(Base):
    __tablename__ = "transaction_classification_rules"
    __table_args__ = (UniqueConstraint("description", "category", "importer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    importer: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


@dataclass
class BalanceSnapshot:
    """An authoritative account balance stated by the source file (e.g. a
    checking/savings ``Balance`` column). ``as_of`` arbitrates between snapshots
    when several files touch the same account — newest wins."""
    amount: float
    as_of: date


@dataclass
class ImportResult:
    transactions: list["TransactionCreate"] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Net change contributed by this file (sum of transaction amounts). Always
    # computed. NOT itself a balance — becomes one only when added to a prior.
    net_delta: float = 0.0
    # Authoritative balance, present only when the file states one.
    snapshot: Optional[BalanceSnapshot] = None
    # Identity of this per-file result, stamped by CsvImporter.run().
    account_id: Optional[int] = None
    filename: Optional[str] = None
    importer: Optional[str] = None
