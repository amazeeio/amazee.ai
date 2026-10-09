"""Checks for numbers and dates that come from outside sources."""

import math
from datetime import date


def is_amount(value) -> bool:
    """A price or count: a finite number, not a bool, not negative."""
    # A JSON body can hold Infinity, which no price column can store.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < math.inf


def per_unit(value, divisor: int = 1) -> float | None:
    """A price per `divisor` units as a price per unit, or None if not an amount.

    Rounded so 0.2 per million is 2e-07, not 2.0000000000000002e-07.
    """
    if not is_amount(value):
        return None
    return float(f"{value / divisor:.12g}")


def as_date(value) -> date | None:
    """`2026-09-08` or `2026-09-08 17:00:00+00:00` as a date; None when not one."""
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None
