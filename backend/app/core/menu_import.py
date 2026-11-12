"""Excel menu import: parse, validate, and build the template.

Sheet "Menu" (required), one row per variant:
    Category | Item | Variant | Price | Short Code | Food Type | Tax Group | HSN/SAC | Description | Prep Area
    - Rows with the same Category + Item become variants of one item (300 ML, 1000 ML ...).
    - Variant blank -> "Regular". Price in rupees. Food Type: veg / non-veg / egg (default veg).
    - Item-level columns are read from the item's first row.
    - Prep Area: where it's made (Shake counter, Kitchen...). Decides which KOT/KDS it goes to.
      New area names are created on import.

Sheet "Addons" (optional), one row per add-on:
    Group | Min | Max | Addon | Price | Items
    - Items: comma-separated item names the group applies to.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from openpyxl import Workbook, load_workbook

from .tax import rupees_to_paise

FOOD_TYPES = {"veg": "veg", "v": "veg", "non-veg": "non_veg", "nonveg": "non_veg", "non veg": "non_veg",
              "nv": "non_veg", "egg": "egg", "e": "egg"}

MENU_COLUMNS = ["Category", "Item", "Variant", "Price", "Short Code", "Food Type", "Tax Group", "HSN/SAC",
                "Description", "Prep Area"]
ADDON_COLUMNS = ["Group", "Min", "Max", "Addon", "Price", "Items"]
MAX_ROWS = 5000


@dataclass
class RowIssue:
    sheet: str
    row: int
    message: str


@dataclass
class ParsedVariant:
    name: str
    price_paise: int
    row: int


@dataclass
class ParsedItem:
    category: str
    name: str
    short_code: str | None
    food_type: str
    tax_group: str | None
    hsn_sac: str | None
    description: str | None
    prep_area: str | None = None
    variants: list[ParsedVariant] = field(default_factory=list)


@dataclass
class ParsedAddon:
    name: str
    price_paise: int


@dataclass
class ParsedAddonGroup:
    name: str
    min_select: int
    max_select: int
    addons: list[ParsedAddon] = field(default_factory=list)
    item_names: list[str] = field(default_factory=list)


@dataclass
class ParsedMenu:
    categories: list[str] = field(default_factory=list)
    items: list[ParsedItem] = field(default_factory=list)
    addon_groups: list[ParsedAddonGroup] = field(default_factory=list)
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> dict:
        return {
            "prep_areas": len({i.prep_area.lower() for i in self.items if i.prep_area}),
            "categories": len(self.categories),
            "items": len(self.items),
            "variants": sum(len(i.variants) for i in self.items),
            "addon_groups": len(self.addon_groups),
            "addons": sum(len(g.addons) for g in self.addon_groups),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
        }


def _clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _header_map(header_row, expected: list[str]) -> dict[str, int]:
    norm = {"".join(_clean(c).lower().split()): i for i, c in enumerate(header_row) if _clean(c)}
    out = {}
    for col in expected:
        key = "".join(col.lower().split())
        if key in norm:
            out[col] = norm[key]
    return out


def _cell(row, idx: dict[str, int], col: str) -> str:
    i = idx.get(col)
    if i is None or i >= len(row):
        return ""
    return _clean(row[i])


def parse_menu_workbook(data: bytes, known_tax_groups: set[str] | None = None) -> ParsedMenu:
    """Parse an uploaded .xlsx. Tax group names are matched case-insensitively against known_tax_groups."""
    result = ParsedMenu()
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - openpyxl raises many types for bad files
        result.errors.append(RowIssue("file", 0, "Could not read the file. Upload an .xlsx Excel file."))
        return result

    sheets = {ws.title.strip().lower(): ws for ws in wb.worksheets}
    menu_ws = sheets.get("menu") or wb.worksheets[0]
    known = {t.lower(): t for t in (known_tax_groups or set())}

    rows = list(menu_ws.iter_rows(values_only=True))
    if not rows:
        result.errors.append(RowIssue("Menu", 1, "Sheet is empty"))
        return result
    idx = _header_map(rows[0], MENU_COLUMNS)
    for required in ("Category", "Item", "Price"):
        if required not in idx:
            result.errors.append(RowIssue("Menu", 1, f"Missing column '{required}'"))
    if result.errors:
        return result
    if len(rows) - 1 > MAX_ROWS:
        result.errors.append(RowIssue("Menu", 1, f"Too many rows (max {MAX_ROWS})"))
        return result

    items: dict[tuple[str, str], ParsedItem] = {}
    item_by_name: dict[str, ParsedItem] = {}
    short_codes: dict[str, int] = {}

    for rnum, row in enumerate(rows[1:], start=2):
        category = _cell(row, idx, "Category")
        name = _cell(row, idx, "Item")
        price_raw = _cell(row, idx, "Price")
        if not (category or name or price_raw):
            continue  # blank row
        if not category:
            result.errors.append(RowIssue("Menu", rnum, "Category is empty"))
            continue
        if not name:
            result.errors.append(RowIssue("Menu", rnum, "Item is empty"))
            continue
        try:
            price = rupees_to_paise(price_raw)
            if price < 0:
                raise ValueError
        except ValueError:
            result.errors.append(RowIssue("Menu", rnum, f"Price '{price_raw}' is not a valid amount"))
            continue

        variant_name = _cell(row, idx, "Variant") or "Regular"
        key = (category.lower(), name.lower())
        item = items.get(key)
        if item is None:
            if name.lower() in item_by_name:
                other = item_by_name[name.lower()]
                result.errors.append(RowIssue(
                    "Menu", rnum, f"Item '{name}' already appears under category '{other.category}'"))
                continue
            food_raw = _cell(row, idx, "Food Type").lower()
            food = FOOD_TYPES.get(food_raw, "veg") if food_raw else "veg"
            if food_raw and food_raw not in FOOD_TYPES:
                result.warnings.append(RowIssue("Menu", rnum, f"Food Type '{food_raw}' not recognised; using veg"))
            tax_raw = _cell(row, idx, "Tax Group")
            tax = None
            if tax_raw:
                if known_tax_groups is not None and tax_raw.lower() not in known:
                    result.errors.append(RowIssue("Menu", rnum, f"Tax group '{tax_raw}' doesn't exist"))
                    continue
                tax = known.get(tax_raw.lower(), tax_raw)
            code = _cell(row, idx, "Short Code").upper() or None
            if code:
                if code in short_codes:
                    result.errors.append(RowIssue(
                        "Menu", rnum, f"Short code '{code}' already used on row {short_codes[code]}"))
                    continue
                short_codes[code] = rnum
            item = ParsedItem(
                category=category, name=name, short_code=code, food_type=food, tax_group=tax,
                hsn_sac=_cell(row, idx, "HSN/SAC") or None,
                description=_cell(row, idx, "Description") or None,
                prep_area=_cell(row, idx, "Prep Area") or None,
            )
            items[key] = item
            item_by_name[name.lower()] = item
            if category not in result.categories:
                result.categories.append(category)
            result.items.append(item)

        if any(v.name.lower() == variant_name.lower() for v in item.variants):
            result.errors.append(RowIssue("Menu", rnum, f"Variant '{variant_name}' repeated for '{name}'"))
            continue
        item.variants.append(ParsedVariant(variant_name, price, rnum))

    addon_ws = sheets.get("addons")
    if addon_ws is not None:
        _parse_addons(addon_ws, result, item_by_name)
    return result


def _parse_addons(ws, result: ParsedMenu, item_by_name: dict[str, ParsedItem]) -> None:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return
    idx = _header_map(rows[0], ADDON_COLUMNS)
    for required in ("Group", "Addon"):
        if required not in idx:
            result.errors.append(RowIssue("Addons", 1, f"Missing column '{required}'"))
            return
    groups: dict[str, ParsedAddonGroup] = {}
    for rnum, row in enumerate(rows[1:], start=2):
        gname = _cell(row, idx, "Group")
        aname = _cell(row, idx, "Addon")
        if not (gname or aname):
            continue
        if not gname or not aname:
            result.errors.append(RowIssue("Addons", rnum, "Group and Addon are both required"))
            continue
        price_raw = _cell(row, idx, "Price") or "0"
        try:
            price = rupees_to_paise(price_raw)
            if price < 0:
                raise ValueError
        except ValueError:
            result.errors.append(RowIssue("Addons", rnum, f"Price '{price_raw}' is not a valid amount"))
            continue
        group = groups.get(gname.lower())
        if group is None:
            try:
                mn = int(_cell(row, idx, "Min") or 0)
                mx = int(_cell(row, idx, "Max") or 1)
            except ValueError:
                result.errors.append(RowIssue("Addons", rnum, "Min and Max must be whole numbers"))
                continue
            if mn < 0 or mx < 1 or mn > mx:
                result.errors.append(RowIssue("Addons", rnum, "Need 0 <= Min <= Max and Max >= 1"))
                continue
            group = ParsedAddonGroup(gname, mn, mx)
            groups[gname.lower()] = group
            result.addon_groups.append(group)
        if any(a.name.lower() == aname.lower() for a in group.addons):
            result.errors.append(RowIssue("Addons", rnum, f"Add-on '{aname}' repeated in group '{gname}'"))
            continue
        group.addons.append(ParsedAddon(aname, price))
        for raw in _cell(row, idx, "Items").split(","):
            iname = raw.strip()
            if not iname:
                continue
            if iname.lower() not in item_by_name:
                result.errors.append(RowIssue("Addons", rnum, f"Item '{iname}' not found in Menu sheet"))
                continue
            canonical = item_by_name[iname.lower()].name
            if canonical not in group.item_names:
                group.item_names.append(canonical)
    for g in result.addon_groups:
        if g.max_select > len(g.addons):
            result.warnings.append(RowIssue(
                "Addons", 0, f"Group '{g.name}' allows {g.max_select} picks but has {len(g.addons)} add-ons"))


def build_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Menu"
    ws.append(MENU_COLUMNS)
    ws.append(["Thick Shakes", "Oreo Shake", "300 ML", 149, "OREO3", "veg", "GST 5%", "996331", "Classic oreo shake",
               "Shake counter"])
    ws.append(["Thick Shakes", "Oreo Shake", "1000 ML", 399, "", "", "", "", "", ""])
    ws.append(["Cold Coffee", "Cold Coffee", "300 ML", 99, "CC3", "veg", "GST 5%", "996331", "", "Shake counter"])
    ws.append(["Snacks", "Peri Peri Fries", "Regular", 129, "FRIES", "veg", "GST 5%", "996331", "", "Kitchen"])
    for col, width in zip("ABCDEFGHIJ", (18, 22, 12, 10, 12, 11, 12, 10, 30, 16)):
        ws.column_dimensions[col].width = width
    ws2 = wb.create_sheet("Addons")
    ws2.append(ADDON_COLUMNS)
    ws2.append(["Extra Toppings", 0, 2, "Choco Chips", 20, "Oreo Shake, Cold Coffee"])
    ws2.append(["Extra Toppings", 0, 2, "Ice Cream Scoop", 40, "Oreo Shake"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
