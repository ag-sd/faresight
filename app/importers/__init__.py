from typing import Callable

from app.importers.capitalone import import_checking_savings_csv as _capitalone_savings
from app.importers.capitalone import import_credit_card_csv as _capitalone_cc

IMPORTERS: dict[str, Callable] = {
    "Capital One Credit Card": _capitalone_cc,
    "Capital One Checking/Savings": _capitalone_savings,
}
