import csv
import io
from abc import ABC, abstractmethod
from typing import Generic, Optional, TypeVar

from app.models import Account, BalanceSnapshot, ImportResult
from app.schemas import TransactionCreate

# Per-file context type. Importers that carry state while parsing a single file
# (e.g. tracking the newest balance) override new_context() to return their own
# dataclass; stateless importers use the default empty dict.
C = TypeVar("C")


class CsvImporter(ABC, Generic[C]):
    """Template Method base for CSV importers.

    The base owns the invariant skeleton — decode, DictReader, the row loop, and
    per-row error capture — and delegates the one varying step (a CSV row → a
    TransactionCreate) to ``parse_row``. State that must persist across rows of a
    single file lives in a fresh per-file *context* (see ``new_context``), so the
    importer instance itself stays stateless and reentrant.
    """

    encoding = "utf-8-sig"

    def skip_lines(self) -> int:
        """Preamble lines to drop before DictReader sees the CSV. Override when
        the source file has a summary header before the real column header."""
        return 0

    def row_start(self) -> int:
        """1-based row number of the first data row in the original file.
        Used as the enumerate start for accurate error messages."""
        return 2

    def new_context(self) -> C:
        """Fresh per-file state bag. Override to return a typed dataclass when an
        importer needs to accumulate state across rows; the default suits the
        stateless case."""
        return {}  # type: ignore[return-value]

    @abstractmethod
    def parse_row(self, row: dict, account: Account, ctx: C) -> Optional[TransactionCreate]:
        """Map one CSV row to a TransactionCreate. Return ``None`` to skip the row
        (no error recorded). Raise ValueError/KeyError to record a row error."""
        ...

    def balance_snapshot(self, ctx: C) -> Optional[BalanceSnapshot]:
        """Authoritative balance for this file, if the source states one. The base
        never inspects the context bag directly — this hook is how a snapshot
        leaves it. Default: the file carries no balance."""
        return None

    def run(
        self,
        file_bytes: bytes,
        account: Account,
        filename: Optional[str] = None,
        importer: Optional[str] = None,
    ) -> ImportResult:
        ctx = self.new_context()
        text = file_bytes.decode(self.encoding)
        skip = self.skip_lines()
        if skip:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[skip:])
        reader = csv.DictReader(io.StringIO(text))
        transactions, errors = [], []

        for i, row in enumerate(reader, start=self.row_start()):
            try:
                tx = self.parse_row(row, account, ctx)
                if tx is not None:
                    transactions.append(tx)
            except (KeyError, ValueError) as e:
                errors.append(f"Row {i}: {e}")

        return ImportResult(
            transactions=transactions,
            errors=errors,
            net_delta=round(sum(tx.amount for tx in transactions), 2),
            snapshot=self.balance_snapshot(ctx),
            account_id=account.id,
            filename=filename,
            importer=importer,
        )

    @staticmethod
    def apply_sign(amount: float, *, debit: bool) -> float:
        """The importer sign convention, in one place: debit → negative,
        credit → positive. Every importer routes its amounts through this,
        whatever shape its source format takes."""
        return -amount if debit else amount

    @staticmethod
    def signed_amount(debit: str, credit: str) -> float:
        """Convenience for separate debit/credit *column* formats. Parses the
        populated column and delegates the sign to apply_sign(). Raises
        ValueError if the row has neither."""
        if debit:
            return CsvImporter.apply_sign(float(debit), debit=True)
        if credit:
            return CsvImporter.apply_sign(float(credit), debit=False)
        raise ValueError("row has neither Debit nor Credit")
