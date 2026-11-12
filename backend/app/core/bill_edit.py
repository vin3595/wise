"""Rules for correcting a bill after it's issued (pure, unit-tested).

A correction keeps the invoice number; the bill's previous version is saved with the reason, who
changed it and who approved it, and every correction is listed in the Bill edits report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class EditFacts:
    items_changed: bool        # quantities, items or discount
    payments_changed: bool
    customer_changed: bool
    old_total_paise: int
    new_total_paise: int
    bill_date: date
    today: date
    shift_open: bool
    is_manager: bool
    locked_through: date | None


def blockers(f: EditFacts) -> str | None:
    """A reason the edit can't happen at all, or None."""
    if f.locked_through is not None and f.bill_date <= f.locked_through:
        return (f"Bills up to {f.locked_through:%d %b %Y} are locked because that period's GST return is filed. "
                "The owner can change the lock date under Bills.")
    if not (f.items_changed or f.payments_changed or f.customer_changed):
        return "Nothing was changed"
    if not f.is_manager and f.bill_date != f.today:
        return "Only a manager can correct an earlier day's bill"
    if not f.is_manager and not f.shift_open and (f.items_changed or f.payments_changed):
        return "This shift is closed and its cash counted, so only a manager can change amounts or payments"
    return None


def approval_needed(f: EditFacts) -> list[str]:
    """Why a manager has to approve (a PIN for cashiers; managers approve their own edits)."""
    out = []
    if f.items_changed:
        out.append("Items or discount changed")
    if f.new_total_paise < f.old_total_paise:
        out.append("Bill total goes down")
    return out


def describe(f: EditFacts) -> list[str]:
    out = []
    if f.items_changed:
        out.append("items")
    if f.payments_changed:
        out.append("payment")
    if f.customer_changed:
        out.append("customer")
    return out
