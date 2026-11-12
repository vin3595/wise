"""Loyalty rules (pure functions, unit-tested). The manager picks: off, points, visit stamps, or both.

Points: a share of each bill comes back as points worth that many rupees (5% of ₹200 = ₹10 of points), used
as a discount on a later bill, up to a cap. Points lapse if the customer doesn't come back within the
expiry period. Stamps: one stamp per bill over a minimum; after N stamps the next bill gets the reward
(a discount up to a set amount, e.g. one free shake). Everything is kept in paise.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

MODES = ("off", "points", "stamps", "both")


@dataclass(frozen=True)
class Rules:
    mode: str = "off"
    earn_bp: int = 500                # 5% of the bill comes back as points
    min_bill_paise: int = 0           # bills below this earn nothing
    redeem_min_paise: int = 5000      # need at least ₹50 of points to use them
    max_redeem_bp: int = 5000         # points pay at most half of a bill
    expiry_days: int = 180            # points lapse after this long without a visit (0 = never)
    stamp_min_paise: int = 10000      # a bill of ₹100 or more earns a stamp
    stamps_needed: int = 10
    reward_paise: int = 15000         # the reward: up to ₹150 off

    @property
    def points(self) -> bool:
        return self.mode in ("points", "both")

    @property
    def stamps(self) -> bool:
        return self.mode in ("stamps", "both")


def earned(r: Rules, paid_paise: int) -> int:
    """Points (in paise) a bill earns, on what the customer actually paid."""
    if not r.points or paid_paise <= 0 or paid_paise < r.min_bill_paise:
        return 0
    return paid_paise * r.earn_bp // 10000


def stamp(r: Rules, paid_paise: int) -> int:
    return 1 if r.stamps and paid_paise >= r.stamp_min_paise and paid_paise > 0 else 0


def redeemable(r: Rules, balance_paise: int, bill_gross_paise: int) -> int:
    """Most points that can be used on this bill (whole rupees)."""
    if not r.points or balance_paise < r.redeem_min_paise or bill_gross_paise <= 0:
        return 0
    cap = bill_gross_paise * r.max_redeem_bp // 10000
    return min(balance_paise, cap) // 100 * 100


def reward(r: Rules, stamps: int, bill_gross_paise: int) -> int:
    """The stamp reward on this bill, if earned (never more than the bill)."""
    if not r.stamps or stamps < r.stamps_needed or bill_gross_paise <= 0:
        return 0
    return min(r.reward_paise, bill_gross_paise)


def lapsed(r: Rules, last_visit: date | None, today: date) -> bool:
    return bool(r.expiry_days and last_visit and today - last_visit > timedelta(days=r.expiry_days))


def check_use(r: Rules, *, points_paise: int, use_reward: bool, balance_paise: int, stamps: int, gross_paise: int) -> str | None:
    """None if this redemption is allowed, else why not."""
    if points_paise:
        if not r.points:
            return "Points aren't switched on"
        if points_paise % 100:
            return "Use points in whole rupees"
        if points_paise > redeemable(r, balance_paise, gross_paise):
            return f"At most ₹{redeemable(r, balance_paise, gross_paise) / 100:g} of points can be used on this bill"
    if use_reward and not reward(r, stamps, gross_paise):
        return "The customer hasn't collected enough stamps yet"
    if points_paise + (reward(r, stamps, gross_paise) if use_reward else 0) > gross_paise:
        return "Points and reward can't be more than the bill"
    return None
