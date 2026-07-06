"""Pure recurring-charge detection — no DB, fully unit-testable.

The router (app/routers/insights.py) groups candidate transactions by
(account_id, description) and feeds each group's dates/amounts through
detect_recurring().
"""
import statistics
from datetime import date, timedelta
from typing import Optional

# Inclusive day-gap bands per cadence.
CADENCE_BANDS = {"weekly": (6, 8), "monthly": (26, 33), "yearly": (350, 380)}

# Multiplier converting one charge at a cadence to a monthly-equivalent amount.
MONTHLY_FACTOR = {"weekly": 52 / 12, "monthly": 1.0, "yearly": 1 / 12}

# Minimum occurrences to establish a cadence. Yearly needs only two points
# (one gap) — three would require three years of history.
MIN_OCCURRENCES = {"weekly": 3, "monthly": 3, "yearly": 2}

# A charge is "active" while as_of is within this factor of the cadence gap
# after the last occurrence; beyond that it's treated as cancelled.
STALE_FACTOR = 1.5


def detect_recurring(dates: list[date], amounts: list[float], as_of: date) -> Optional[dict]:
    """Classify one (account, description) group as a recurring charge.

    Returns None unless the occurrence gaps are regular (every gap inside one
    cadence band), the occurrence count meets the cadence minimum, and the
    charge is still active relative to ``as_of``.
    """
    if len(dates) < 2:
        return None

    ordered = sorted(zip(dates, amounts), key=lambda p: p[0])
    dates = [d for d, _ in ordered]
    amounts = [a for _, a in ordered]

    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    median_gap = statistics.median(gaps)

    cadence = next(
        (name for name, (lo, hi) in CADENCE_BANDS.items() if lo <= median_gap <= hi),
        None,
    )
    if cadence is None:
        return None
    if len(dates) < MIN_OCCURRENCES[cadence]:
        return None

    # Strict v1: every gap must sit inside the band (a missed month breaks it).
    lo, hi = CADENCE_BANDS[cadence]
    if not all(lo <= g <= hi for g in gaps):
        return None

    # Stale = likely cancelled; don't report it.
    if (as_of - dates[-1]).days > STALE_FACTOR * median_gap:
        return None

    price_changed = abs(abs(amounts[-1]) - abs(amounts[-2])) > 0.01

    return {
        "cadence": cadence,
        "amount": amounts[-1],
        "last_date": dates[-1],
        "next_expected": dates[-1] + timedelta(days=round(median_gap)),
        "occurrences": len(dates),
        "price_changed": price_changed,
        "previous_amount": amounts[-2] if price_changed else None,
    }
