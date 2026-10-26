"""Money helpers.

Amounts travel through the API as decimals with at most two fractional digits
(``"150.00"``) and are persisted as exact integers in minor units (``15000``).
Keeping the conversion in one place avoids scattering ``* 100`` across the code.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MINOR_UNITS_PER_MAJOR = 100
_QUANTUM = Decimal("0.01")


def to_minor(amount: Decimal) -> int:
    """Convert a decimal major-unit amount (``Decimal('12.34')``) to minor units (``1234``)."""
    quantised = Decimal(amount).quantize(_QUANTUM, rounding=ROUND_HALF_UP)
    return int(quantised * MINOR_UNITS_PER_MAJOR)


def to_decimal(minor: int) -> Decimal:
    """Convert minor units (``1234``) to a two-decimal ``Decimal`` (``Decimal('12.34')``)."""
    return (Decimal(int(minor)) / MINOR_UNITS_PER_MAJOR).quantize(_QUANTUM)


def format_money(minor: int) -> str:
    """Render minor units as a fixed two-decimal string (``"12.34"``)."""
    return f"{to_decimal(minor):.2f}"
