"""Kitchen ticket (KOT) rules: splitting by preparation area, status flow, order status.

KOT status:   new -> preparing -> ready -> served
              (one step back is allowed to fix a mis-tap: preparing->new, ready->preparing)
Order status is derived from its KOTs and shown on the customer screen:
              new | preparing | ready | completed
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TypeVar

IST = timezone(timedelta(hours=5, minutes=30))

KOT_STATUSES = ("new", "preparing", "ready", "served")
ORDER_TYPES = ("dine_in", "takeaway", "delivery")

_FORWARD = {"new": "preparing", "preparing": "ready", "ready": "served"}
_BACK = {"preparing": "new", "ready": "preparing"}


class TransitionError(ValueError):
    pass


def check_transition(current: str, target: str) -> None:
    if current == target:
        raise TransitionError(f"Ticket is already {current}")
    if _FORWARD.get(current) == target or _BACK.get(current) == target:
        return
    # Kitchen may jump straight from new to ready for quick items.
    if current == "new" and target == "ready":
        return
    raise TransitionError(f"Can't move a ticket from {current} to {target}")


def next_status(current: str) -> str | None:
    return _FORWARD.get(current)


def order_status(kot_statuses: Iterable[str]) -> str:
    statuses = list(kot_statuses)
    if not statuses:
        return "new"
    if all(s == "served" for s in statuses):
        return "completed"
    if all(s in ("ready", "served") for s in statuses):
        return "ready"
    if all(s == "new" for s in statuses):
        return "new"
    return "preparing"


T = TypeVar("T")


@dataclass(frozen=True)
class AreaKey:
    id: str | None
    name: str


UNASSIGNED = AreaKey(None, "Kitchen")


def split_by_area(lines: Sequence[T], area_of: Callable[[T], AreaKey | None]) -> list[tuple[AreaKey, list[T]]]:
    """Group order lines into one KOT per preparation area, keeping first-seen order.
    Lines whose item has no area go to a default 'Kitchen' ticket."""
    groups: dict[AreaKey, list[T]] = {}
    for line in lines:
        key = area_of(line) or UNASSIGNED
        groups.setdefault(key, []).append(line)
    return list(groups.items())


def business_date(now: datetime | None = None, day_starts_hour: int = 4) -> date:
    """Restaurants trading past midnight keep the same business day until 4 am IST,
    so token numbers don't reset at 12:00 during late service."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    if now.hour < day_starts_hour:
        now -= timedelta(days=1)
    return now.date()


def ticket_age_level(started: datetime, now: datetime, warn_after_min: int = 8, late_after_min: int = 15) -> str:
    """'ok' | 'warn' | 'late' for colouring KDS tickets."""
    mins = (now - started).total_seconds() / 60
    if mins >= late_after_min:
        return "late"
    if mins >= warn_after_min:
        return "warn"
    return "ok"


_CAR = re.compile(r"^[A-Z0-9]{4,10}$")


def normalize_car_number(raw: str | None) -> str | None:
    """'rj 19 ca 1234' -> 'RJ19CA1234'. The last 4 digits alone are accepted too."""
    if raw is None:
        return None
    value = re.sub(r"[\s\-.]", "", raw).upper()
    if not value:
        return None
    if not _CAR.match(value):
        raise ValueError("Car number should be 4-10 letters/numbers, e.g. RJ19CA1234 or 1234")
    return value


def car_matches(car_number: str | None, query: str) -> bool:
    """Search by any part, usually the last 4 digits."""
    if not car_number:
        return False
    q = re.sub(r"[\s\-.]", "", query).upper()
    return bool(q) and q in car_number
