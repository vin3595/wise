import io

from openpyxl import Workbook

from app.core.menu_import import MENU_COLUMNS, build_template, parse_menu_workbook
from app.core.menu_resolve import Override, resolve_variant


def make_xlsx(menu_rows, addon_rows=None, menu_header=None, addon_header=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Menu"
    ws.append(menu_header or MENU_COLUMNS)
    for r in menu_rows:
        ws.append(r)
    if addon_rows is not None:
        ws2 = wb.create_sheet("Addons")
        ws2.append(addon_header or ["Group", "Min", "Max", "Addon", "Price", "Items"])
        for r in addon_rows:
            ws2.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_template_parses_cleanly():
    parsed = parse_menu_workbook(build_template(), known_tax_groups={"GST 5%"})
    assert parsed.ok, parsed.errors
    assert parsed.summary() == {"prep_areas": 2, "categories": 3, "items": 3, "variants": 4, "addon_groups": 1,
                                "addons": 2, "errors": 0, "warnings": 0}
    oreo = parsed.items[0]
    assert [(v.name, v.price_paise) for v in oreo.variants] == [("300 ML", 14900), ("1000 ML", 39900)]
    assert oreo.short_code == "OREO3" and oreo.tax_group == "GST 5%" and oreo.prep_area == "Shake counter"
    assert parsed.items[2].prep_area == "Kitchen"
    assert parsed.addon_groups[0].item_names == ["Oreo Shake", "Cold Coffee"]


def test_variants_group_and_defaults():
    parsed = parse_menu_workbook(make_xlsx([
        ["Shakes", "Mango Shake", "", "120", "", "", "", "", ""],
        ["Shakes", "Kesar Shake", "Small", 99.5, "ks", "NV", "gst 5%", "", "Saffron"],
    ]), known_tax_groups={"GST 5%"})
    assert parsed.ok
    mango, kesar = parsed.items
    assert mango.variants[0].name == "Regular" and mango.food_type == "veg"
    assert kesar.variants[0].price_paise == 9950 and kesar.food_type == "non_veg"
    assert kesar.short_code == "KS" and kesar.tax_group == "GST 5%"


def test_row_errors_reported_with_row_numbers():
    parsed = parse_menu_workbook(make_xlsx([
        ["Shakes", "", "", 100],                     # row 2: no item
        ["Shakes", "Oreo", "", "abc"],               # row 3: bad price
        ["Shakes", "Oreo", "300 ML", 100, "OR"],     # row 4: ok
        ["Shakes", "Oreo", "300 ML", 120],           # row 5: duplicate variant
        ["Coffee", "Oreo", "", 90],                  # row 6: same item in another category
        ["Coffee", "Latte", "", 90, "OR"],           # row 7: duplicate short code
        ["Coffee", "Mocha", "", 90, "", "", "GST 12%"],  # row 8: unknown tax group
        [None, None, None, None],                    # blank row ignored
    ]), known_tax_groups={"GST 5%"})
    assert not parsed.ok
    rows = {e.row: e.message for e in parsed.errors}
    assert set(rows) == {2, 3, 5, 6, 7, 8}
    assert "Price" in rows[3] and "repeated" in rows[5] and "already appears" in rows[6]
    assert "Short code" in rows[7] and "doesn't exist" in rows[8]


def test_missing_columns_and_bad_file():
    parsed = parse_menu_workbook(make_xlsx([], menu_header=["Name", "Cost"]))
    assert {e.message for e in parsed.errors} == {"Missing column 'Category'", "Missing column 'Item'",
                                                  "Missing column 'Price'"}
    assert not parse_menu_workbook(b"not an excel file").ok


def test_header_matching_is_loose():
    parsed = parse_menu_workbook(make_xlsx([["Shakes", "Oreo", "", 100]],
                                           menu_header=["category", " ITEM ", "variant", "price"]))
    assert parsed.ok and parsed.items[0].name == "Oreo"


def test_addon_errors():
    parsed = parse_menu_workbook(make_xlsx(
        [["Shakes", "Oreo", "", 100]],
        [["Toppings", 2, 1, "Chips", 10, "Oreo"],        # min > max
         ["Extras", 0, 2, "Scoop", 30, "Oreo, Ghost"],   # unknown item
         ["Extras", 0, 2, "Scoop", 30, ""]],             # duplicate addon
    ))
    msgs = [e.message for e in parsed.errors]
    assert any("Min" in m for m in msgs)
    assert any("Ghost" in m for m in msgs)
    assert any("repeated" in m for m in msgs)


def test_resolve_variant():
    assert resolve_variant(True, 14900, True, True, None) == (True, 14900)
    assert resolve_variant(True, 14900, True, True, Override(price_paise=15900)) == (True, 15900)
    assert resolve_variant(True, 14900, True, True, Override(is_active=False)) == (False, 14900)
    # Outlet can't switch on what the brand has switched off.
    assert resolve_variant(False, 14900, True, True, Override(is_active=True)) == (False, 14900)
    assert resolve_variant(True, 14900, False, True, None)[0] is False
    assert resolve_variant(True, 14900, True, False, None)[0] is False
