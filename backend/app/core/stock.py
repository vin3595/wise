"""Stock maths (pure functions, unit-tested).

Quantities are whole numbers of thousandths of a base unit ("milli"): 12.5 g = 12500, 1 cup = 1000.
Base units are g, ml and pc; purchase units (kg, litre, a box of 24, a 500 g packet) convert to them.
Cost is kept as paise per 1000 base units (per kg, per litre, per 1000 pieces), as a weighted average.
"""
from __future__ import annotations

from dataclasses import dataclass

BASE_UNITS = ("g", "ml", "pc")
# Standard purchase units: name -> (base unit, how many base units in one).
STANDARD_UNITS: dict[str, tuple[str, int]] = {
    "g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "litre": ("ml", 1000),
    "pc": ("pc", 1), "dozen": ("pc", 12),
}
M = 1000  # milli per unit


def _div(n: int, d: int) -> int:
    """Round half away from zero."""
    sign = -1 if (n < 0) != (d < 0) else 1
    n, d = abs(n), abs(d)
    q, r = divmod(n, d)
    return sign * (q + (1 if r * 2 >= d else 0))


def parse_qty(text: str) -> int:
    """'12.5' -> 12500 milli. At most 3 decimals."""
    t = text.strip().replace(",", "")
    if not t or t.count(".") > 1 or not t.replace(".", "").isdigit():
        raise ValueError(f"'{text}' isn't a quantity")
    whole, _, frac = t.partition(".")
    if len(frac) > 3:
        raise ValueError("Use at most 3 decimals")
    return int(whole or "0") * M + int((frac + "000")[:3])


def fmt_qty(milli: int, unit: str) -> str:
    """12500 g -> '12.5 g'; 2500000 g -> '2.5 kg'; 3000 pc -> '3 pc'."""
    big = {"g": "kg", "ml": "l"}.get(unit)
    v, u = milli, unit
    if big and abs(milli) >= 1000 * M:
        v, u = _div(milli, 1000), big
    s = f"{v / M:.3f}".rstrip("0").rstrip(".")
    return f"{s} {u}"


def to_base(qty_milli: int, unit_factor: int) -> int:
    """qty in a purchase unit (milli) -> base-unit milli. 2.5 kg (2500) x 1000 = 2,500,000 (2500 g)."""
    return qty_milli * unit_factor


def unit_cost_k(amount_paise: int, base_milli: int) -> int:
    """Paise per 1000 base units for a purchase line: ₹450 for 1 kg -> 45000."""
    if base_milli <= 0:
        raise ValueError("Quantity must be more than zero")
    return _div(amount_paise * M * 1000, base_milli)


def value_paise(base_milli: int, cost_k: int) -> int:
    """Value of a quantity at a cost per 1000 base units: 250 g at ₹450/kg -> ₹112.50."""
    return _div(base_milli * cost_k, M * 1000)


def weighted_cost(old_milli: int, old_cost_k: int, in_milli: int, in_cost_k: int) -> int:
    """New average cost after receiving stock. Stock already below zero (sold before it was entered)
    doesn't drag the average: the new purchase price is used."""
    if in_milli <= 0:
        return old_cost_k
    if old_milli <= 0:
        return in_cost_k
    return _div(old_milli * old_cost_k + in_milli * in_cost_k, old_milli + in_milli)


def scale(qty_milli: int, factor_bp: int) -> int:
    """Copy a recipe to another size: 300 ml recipe x 33333 bp (3.3333x) for 1000 ml."""
    return _div(qty_milli * factor_bp, 10000)


@dataclass(frozen=True)
class Use:
    stock_item_id: str
    qty_milli: int


def consumption(lines: list[tuple[int, list[Use]]]) -> dict[str, int]:
    """Total stock used by bill lines: [(qty sold, recipe for one), ...] -> {stock item: milli}."""
    out: dict[str, int] = {}
    for qty, recipe in lines:
        for u in recipe:
            out[u.stock_item_id] = out.get(u.stock_item_id, 0) + u.qty_milli * qty
    return out


def recipe_cost(recipe: list[Use], costs_k: dict[str, int]) -> int:
    """Cost in paise of making one: each ingredient at its current average cost."""
    return sum(value_paise(u.qty_milli, costs_k.get(u.stock_item_id, 0)) for u in recipe)


def margin_bp(price_excl_tax: int, cost: int) -> int | None:
    if price_excl_tax <= 0:
        return None
    return _div((price_excl_tax - cost) * 10000, price_excl_tax)


def reorder_qty(on_hand: int, min_level: int, reorder: int) -> int:
    """How much to order when at or below the minimum: the set reorder quantity, else enough to reach
    twice the minimum. Nothing if above the minimum or no minimum is set."""
    if min_level <= 0 or on_hand > min_level:
        return 0
    return reorder if reorder > 0 else max(2 * min_level - on_hand, 0)


def variance(expected: int, counted: int) -> int:
    """Counted minus expected: negative = short (used more than the recipes say, wasted or missing)."""
    return counted - expected


# ------------------------------------------------------------------ prep items made from other items (sauces, bases)

MAX_PREP_DEPTH = 6


class PrepLoop(ValueError):
    pass


def per_unit(qty_milli: int, batch_use_milli: int, batch_yield_milli: int) -> int:
    """How much of an ingredient goes into qty of a prep item, from its batch recipe."""
    return _div(qty_milli * batch_use_milli, batch_yield_milli)


def explode(usage: dict[str, int], prep_recipes: dict[str, list[Use]], yields: dict[str, int],
            on_sale: set[str], depth: int = 0) -> dict[str, int]:
    """Prep items marked 'made fresh when sold' don't keep stock: replace them by what they're made of,
    through as many levels as there are (a base inside a sauce inside a shake)."""
    if depth > MAX_PREP_DEPTH:
        raise PrepLoop("Prep items use each other in a loop")
    out: dict[str, int] = {}
    nested: dict[str, int] = {}
    for sid, q in usage.items():
        if sid in on_sale and prep_recipes.get(sid) and yields.get(sid):
            for u in prep_recipes[sid]:
                nested[u.stock_item_id] = nested.get(u.stock_item_id, 0) + per_unit(q, u.qty_milli, yields[sid])
        else:
            out[sid] = out.get(sid, 0) + q
    if nested:
        for sid, q in explode(nested, prep_recipes, yields, on_sale, depth + 1).items():
            out[sid] = out.get(sid, 0) + q
    return out


def finds_loop(owner: str, lines: list[str], prep_recipes: dict[str, list[Use]]) -> bool:
    """Would giving `owner` these ingredients make a prep item that (through others) uses itself?"""
    seen: set[str] = set()
    todo = list(lines)
    while todo:
        sid = todo.pop()
        if sid == owner:
            return True
        if sid in seen:
            continue
        seen.add(sid)
        todo.extend(u.stock_item_id for u in prep_recipes.get(sid, []))
    return False


def rolled_cost_k(sid: str, prep_recipes: dict[str, list[Use]], yields: dict[str, int], actual_k: dict[str, int],
                  _depth: int = 0) -> int:
    """Cost of one base unit (x1000) of a prep item from its batch recipe at today's ingredient costs.
    Used until a batch has actually been made (then the real average cost is known), and for
    'made fresh when sold' items, which never have stock of their own."""
    rec, y = prep_recipes.get(sid), yields.get(sid)
    if not rec or not y or _depth > MAX_PREP_DEPTH:
        return actual_k.get(sid, 0)
    total = 0
    for u in rec:
        k = actual_k.get(u.stock_item_id) or rolled_cost_k(u.stock_item_id, prep_recipes, yields, actual_k, _depth + 1)
        total += value_paise(u.qty_milli, k)
    return unit_cost_k(total, y)
