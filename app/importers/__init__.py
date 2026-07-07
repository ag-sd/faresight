from typing import Callable

from app.importers.amex import import_credit_card_csv as _amex_cc
from app.importers.amex import import_savings_csv as _amex_savings
from app.importers.bofa import import_checking_savings_csv as _bofa_checking
from app.importers.bofa import import_credit_card_csv as _bofa_cc
from app.importers.capitalone import import_checking_savings_csv as _capitalone_savings
from app.importers.capitalone import import_credit_card_csv as _capitalone_cc
from app.importers.chase import import_credit_card_csv as _chase_cc
from app.importers.sofi import import_checking_savings_csv as _sofi

IMPORTERS: dict[str, Callable] = {
    "Capital One Credit Card": _capitalone_cc,
    "Capital One Checking/Savings": _capitalone_savings,
    "Bank of America Credit Card": _bofa_cc,
    "Bank of America Checking/Savings": _bofa_checking,
    "Chase Credit Card": _chase_cc,
    "SoFi Checking/Savings": _sofi,
    "American Express Savings": _amex_savings,
    "American Express Credit Card": _amex_cc,
}
