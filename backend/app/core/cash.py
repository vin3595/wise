"""Counting the cash drawer by denomination (pure, unit-tested)."""
from __future__ import annotations

# Indian coins and notes in everyday use, in rupees. Key = c<value> for coins, n<value> for notes.
COINS = (1, 2, 5, 10, 20)
NOTES = (10, 20, 50, 100, 200, 500)
DENOMS = {f"c{v}": v for v in COINS} | {f"n{v}": v for v in NOTES}
MAX_PIECES = 100_000


def clean(counts: dict[str, int] | None) -> dict[str, int] | None:
    """Drops zero rows and refuses unknown denominations or silly counts."""
    if counts is None:
        return None
    out = {}
    for k, n in counts.items():
        if k not in DENOMS:
            raise ValueError(f"Unknown coin or note: {k}")
        if not isinstance(n, int) or n < 0 or n > MAX_PIECES:
            raise ValueError(f"Enter a count between 0 and {MAX_PIECES} for ₹{DENOMS[k]}")
        if n:
            out[k] = n
    return out


def total_paise(counts: dict[str, int]) -> int:
    return sum(DENOMS[k] * n * 100 for k, n in counts.items())


def difference(expected: int, counted: int, tolerance: int = 0) -> int | None:
    """counted - expected when it's more than the tolerance either way (negative = short), else None."""
    d = counted - expected
    return d if abs(d) > tolerance else None


def describe_difference(d: int) -> str:
    r = abs(d) / 100
    amount = f"{r:,.0f}" if r == int(r) else f"{r:,.2f}"
    return f"Rs {amount} {'short' if d < 0 else 'extra'}"
