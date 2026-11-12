"""Rules for counters that keep billing while the box can't be reached (pure functions, unit-tested).

Each such counter owns its own invoice series: the outlet code's first three characters plus a letter
(SHKW -> SHKA, SHKB, ...). Numbers made offline therefore never clash with the box's own series or with
another counter's, and every series stays consecutive, as GST Rule 46 requires. Token numbers made
offline come from a separate block per counter (A: 1001+, B: 2001+), well above a day's normal tokens.
"""
from __future__ import annotations

import string
from dataclasses import dataclass

LETTERS = string.ascii_uppercase


def series_for(code: str, taken: set[str]) -> tuple[str, str]:
    """(letter, series) for the next counter at an outlet with this invoice code."""
    code = code.upper()
    for letter in LETTERS:
        series = code[:3] + letter
        if letter in taken or series == code:
            continue
        return letter, series
    raise ValueError("This outlet already has the most offline counters allowed (25)")


def token_base(letter: str) -> int:
    return (LETTERS.index(letter) + 1) * 1000


@dataclass(frozen=True)
class Seen:
    """One line as the counter billed it and as the box's menu has it now."""
    name: str
    billed_unit_paise: int
    menu_unit_paise: int | None   # None: item no longer on this outlet's menu
    billed_rate_bp: int
    menu_rate_bp: int


def differences(lines: list[Seen]) -> list[str]:
    """Plain-language notes for the manager when an offline bill used a price or GST rate
    that has changed on the box since the counter last saw the menu."""
    out = []
    for s in lines:
        if s.menu_unit_paise is None:
            out.append(f"{s.name}: no longer on the menu")
        elif s.menu_unit_paise != s.billed_unit_paise:
            out.append(f"{s.name}: billed at ₹{s.billed_unit_paise / 100:g}, menu price now ₹{s.menu_unit_paise / 100:g}")
        if s.billed_rate_bp != s.menu_rate_bp:
            out.append(f"{s.name}: billed with {s.billed_rate_bp / 100:g}% GST, menu now {s.menu_rate_bp / 100:g}%")
    return out
