from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.models import AccountType, SourceFrequency


class TransactionCreate(BaseModel):
    # `model_` fields collide with Pydantic's protected namespace; opt out.
    model_config = {"protected_namespaces": ()}

    date: date
    description: str
    amount: float
    category: str
    account_id: Optional[int] = None
    model_category: Optional[str] = None
    model_confidence: Optional[int] = -1
    user_modified_category: bool = False
    # Stable bank transaction ID when the importer supplies one; drives dedupe.
    reference_number: Optional[str] = None


class TransactionUpdate(BaseModel):
    model_config = {"protected_namespaces": ()}

    date: Optional[date] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    account_id: Optional[int] = None
    model_category: Optional[str] = None
    model_confidence: Optional[int] = None
    user_modified_category: Optional[bool] = None


class TransactionCreateWithFile(TransactionCreate):
    file_id: int


class TransactionOut(TransactionCreate):
    id: int
    file_id: int
    created_at: datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}

    @field_validator("model_confidence", mode="after")
    @classmethod
    def _mask_pending(cls, v: Optional[int]) -> Optional[int]:
        return None if v == -1 else v


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
    current_balance: Optional[float] = None
    default_importer: Optional[str] = None


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
    default_importer: Optional[str] = None


class AccountOut(AccountCreate):
    id: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── File imports ─────────────────────────────────────────────────────────────

class FileImportOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    filename: str
    rows_seen: int
    rows_persisted: int
    rows_skipped: int = 0
    loaded_at: datetime
    account_id: Optional[int] = None
    importer: Optional[str] = None


# ── Categories ───────────────────────────────────────────────────────────────

VALID_BUCKETS = {"income", "spend", "internal"}


class CategoryCreate(BaseModel):
    name: str
    color: str = "#6c757d"
    bucket: str  # validated against VALID_BUCKETS in the router
    description: Optional[str] = None


class CategoryUpdate(BaseModel):
    color: Optional[str] = None
    bucket: Optional[str] = None
    description: Optional[str] = None


class CategoryOut(CategoryCreate):
    id: int
    sort_order: int

    model_config = {"from_attributes": True}


# ── Rules ─────────────────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    description: str
    category: str
    importer: str


class RuleOut(RuleCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Summaries ─────────────────────────────────────────────────────────────────

class CategorySummary(BaseModel):
    category: str
    total: float


class MonthlySummary(BaseModel):
    year: int
    month: int
    total: float


class CashFlowPoint(BaseModel):
    """Raw signed sums per month: income positive, spend negative, net = income + spend."""
    year: int
    month: int
    income: float
    spend: float
    net: float


class BadgeSummary(BaseModel):
    net_worth: float
    assets: float
    liabilities: float
    month_income: float
    month_spend: float
    prev_month_income: float
    prev_month_spend: float
    savings_rate: Optional[float]  # 0–1; None when month_income == 0


# ── Insights ──────────────────────────────────────────────────────────────────

class CategoryTrend(BaseModel):
    """Raw signed sums (spend negative); delta = current − previous, so more
    negative means spending increased."""
    category: str
    current: float
    previous: float
    delta: float
    avg_3mo: Optional[float]  # None when no data precedes the selected month


class MerchantSummary(BaseModel):
    description: str
    total: float  # raw signed sum
    count: int


class RecurringItem(BaseModel):
    description: str
    account_id: Optional[int]
    cadence: str  # weekly | monthly | yearly
    amount: float  # latest charge, raw negative
    last_date: date
    next_expected: date
    occurrences: int
    price_changed: bool
    previous_amount: Optional[float]  # set only when price_changed


class RecurringOut(BaseModel):
    items: list[RecurringItem]
    monthly_total: float  # monthly-equivalent, raw negative


# ── Pagination ────────────────────────────────────────────────────────────────

class PaginatedTransactions(BaseModel):
    data: list[TransactionOut]
    limit: int
    offset: int
    total: int


class PaginatedFileImports(BaseModel):
    data: list[FileImportOut]
    limit: int
    offset: int
    total: int
