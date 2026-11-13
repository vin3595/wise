"""What a cash in / out was for, and how it reads in reports and the accounts export.

Out of the drawer: an expense (with a category), a drawing (handed to the owner, or deposited in the bank),
a supplier payment, or something else. Into the drawer: an owner top-up, change from the bank, or something else.
"""
from __future__ import annotations

OUT = ("expense", "drawing", "supplier", "other")
IN = ("topup", "change", "other")
HANDED = ("owner", "bank")
DEFAULT_CATEGORIES = ("Milk", "Ice", "Fruits", "Groceries", "Packaging", "Gas", "Staff food / tea", "Transport",
                      "Repairs", "Cleaning", "Electricity", "Other")

LABEL = {"expense": "Expense", "drawing": "Drawing", "supplier": "Paid to supplier", "topup": "Owner top-up",
         "change": "Change from bank", "other": "Other"}


def type_label(kind: str, purpose: str | None, handed: str | None) -> str:
    if purpose == "drawing":
        return "Drawing: deposited in bank" if handed == "bank" else "Drawing: given to owner"
    if purpose is None:
        return "Cash out" if kind == "out" else "Cash in"
    return LABEL.get(purpose, purpose)


def describe(kind: str, purpose: str | None, category: str | None, handed: str | None, paid_to: str | None,
             note: str | None) -> str:
    """One line for the shift list and the old reason column, e.g. 'Expense: Milk (morning delivery)'."""
    head = type_label(kind, purpose, handed)
    detail = category if purpose == "expense" else paid_to
    text = f"{head}: {detail}" if detail else head
    if note and note.strip() and note.strip() != detail:
        text += f" ({note.strip()})"
    return text[:120]
