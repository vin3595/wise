"""Which slip a KOT still needs (pure, unit-tested)."""
from __future__ import annotations

from datetime import datetime


def job_kind(status: str, printed_at: datetime | None, cancel_printed_at: datetime | None,
             reprint_pending: bool) -> str | None:
    """kot = first print; reprint = asked to print again; cancel = the kitchen already has a printed
    slip for a KOT that was cancelled, so it gets a CANCELLED slip. None = nothing to print."""
    if status == "cancelled":
        return "cancel" if printed_at is not None and cancel_printed_at is None else None
    if printed_at is None:
        return "kot"
    return "reprint" if reprint_pending else None
