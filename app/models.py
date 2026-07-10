import enum
import hashlib
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
    # Raw category label from the bank's export; feeds the categorizer's LLM
    # hint only, never displayed. The canonical display category is model_category.
    bank_category: Mapped[str] = mapped_column(String(100), nullable=False)
    account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=True)
    # Canonical display category: written by the categorizer worker, rules, or
    # user edits; user edits set user_modified_category so the worker never
    # overwrites them.
    model_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=-1)
    user_modified_category: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Immutable import-identity hash (see dedup_hash_for). Stamped at insert
    # time and never recomputed on edit, so re-importing a bank file cannot
    # re-insert a row the user has since modified. Non-unique on purpose:
    # legitimate duplicates exist (two identical bus fares in one day) —
    # re-import idempotency uses occurrence counting, not a constraint.
    dedup_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # Stable bank-assigned transaction ID, when the source file carries one (e.g.
    # Bank of America's Reference Number). Feeds dedup_hash_for() so re-imports
    # survive pending→posted description/amount rewrites. NULL for sources without one.
    reference_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    file_id: Mapped[int] = mapped_column(Integer, ForeignKey("file_imports.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


def dedup_hash_for(
    account_id: Optional[int],
    tx_date: date,
    description: str,
    amount: float,
    reference: Optional[str] = None,
) -> str:
    """Canonical import identity of a transaction, shared by the import path,
    manual creation, and the migration backfill.

    When the source file carries a stable bank transaction ID (``reference``),
    the identity is that ID alone (namespaced + account-scoped) — this survives
    the bank rewording a description or restating an amount between exports
    (pending → posted), which the content key below cannot.

    Otherwise the identity is the content tuple. Amount is formatted to two
    decimals so float repr noise never splits identities. Limitation: without a
    reference, a reworded description still re-imports as new.
    """
    if reference:
        key = f"{account_id}|ref|{reference}"
    else:
        key = f"{account_id}|{tx_date.isoformat()}|{description}|{amount:.2f}"
    return hashlib.sha256(key.encode()).hexdigest()


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
    default_importer: Mapped[str] = mapped_column(String(100), nullable=False)


class FileImport(Base):
    __tablename__ = "file_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_persisted: Mapped[int] = mapped_column(Integer, nullable=False)
    # Rows dropped by the re-import dedupe guard: rows_seen = persisted + errors + skipped.
    rows_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # SHA-256 of the raw uploaded bytes — exact-file re-upload short-circuit.
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=True)
    importer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class BalanceHistory(Base):
    __tablename__ = "balance_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    # Effective date of this balance: snapshot files state it; delta files use the
    # newest transaction that produced the balance.
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#6c757d", server_default="'#6c757d'")
    # income | spend | internal
    bucket: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Controls LLM prompt order and display order. Lower = earlier. Stable sort_order
    # keeps the prompt identical across runs, preserving Ollama prompt cache hits.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class Rule(Base):
    __tablename__ = "transaction_classification_rules"
    __table_args__ = (UniqueConstraint("description", "category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
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
