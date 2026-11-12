"""Merge a brand's master menu with one outlet's overrides."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Override:
    is_active: bool | None = None     # None = follow master
    price_paise: int | None = None    # None = master price


def resolve_variant(master_active: bool, master_price: int, item_active: bool, category_active: bool,
                    override: Override | None) -> tuple[bool, int]:
    """Returns (available_at_outlet, price_at_outlet).

    A variant is sold at an outlet only if its category, item and variant are active in the
    master menu, and the outlet hasn't switched it off. An outlet can switch an item off but
    can't switch on something the brand has deactivated.
    """
    available = master_active and item_active and category_active
    price = master_price
    if override is not None:
        if override.is_active is False:
            available = False
        if override.price_paise is not None:
            price = override.price_paise
    return available, price
