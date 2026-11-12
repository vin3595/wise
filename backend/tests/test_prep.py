import pytest

from app.core.stock import PrepLoop, Use, explode, finds_loop, per_unit, rolled_cost_k, value_paise

# Green chutney: one batch of 500 g coriander, 100 g mint, 20 g chilli makes 600 g.
CHUTNEY = [Use("cor", 500_000), Use("mint", 100_000), Use("chilli", 20_000)]
# Pizza base: 1000 g flour + 600 ml water + 20 g yeast makes 8 bases; a pizza uses 1 base + 80 g sauce.
SAUCE = [Use("tomato", 1_000_000), Use("oil", 50_000)]          # makes 800 g
BOOK = {"chutney": CHUTNEY, "sauce": SAUCE, "base": [Use("flour", 1_000_000), Use("water", 600_000), Use("yeast", 20_000)]}
YIELDS = {"chutney": 600_000, "sauce": 800_000, "base": 8_000}
COST = {"cor": 8_000, "mint": 20_000, "chilli": 10_000, "tomato": 4_000, "oil": 15_000,
        "flour": 4_500, "water": 0, "yeast": 40_000}   # paise per kg / litre: coriander ₹80/kg ...


def test_per_unit():
    assert per_unit(30_000, 500_000, 600_000) == 25_000       # 30 g chutney holds 25 g coriander


def test_made_fresh_explodes_to_ingredients():
    used = {"chutney": 30_000, "bread": 2_000}
    assert explode(used, BOOK, YIELDS, {"chutney"}) == {"bread": 2_000, "cor": 25_000, "mint": 5_000, "chilli": 1_000}
    assert explode(used, BOOK, YIELDS, set()) == used            # batch-made: the chutney itself comes out of stock


def test_nested_prep():
    book = dict(BOOK, pesto_mayo=[Use("chutney", 200_000), Use("mayo", 300_000)])   # makes 500 g
    y = dict(YIELDS, pesto_mayo=500_000)
    out = explode({"pesto_mayo": 50_000}, book, y, {"pesto_mayo", "chutney"})
    assert out == {"mayo": 30_000, "cor": 16_667, "mint": 3_333, "chilli": 667}
    assert explode({"pesto_mayo": 50_000}, book, y, {"pesto_mayo"}) == {"chutney": 20_000, "mayo": 30_000}


def test_loop():
    assert finds_loop("sauce", ["tomato", "chutney"], {"chutney": [Use("sauce", 1)]})
    assert not finds_loop("sauce", ["tomato", "oil"], BOOK)
    with pytest.raises(PrepLoop):
        explode({"a": 1000}, {"a": [Use("b", 1000)], "b": [Use("a", 1000)]}, {"a": 1000, "b": 1000}, {"a", "b"})


def test_rolled_cost():
    k = rolled_cost_k("chutney", BOOK, YIELDS, COST)
    batch = 4000 + 2000 + 200        # ₹40 coriander + ₹20 mint + ₹2 chilli
    assert value_paise(600_000, k) == batch
    assert value_paise(30_000, k) == 310                         # 30 g on a sandwich: ₹3.10
    base_k = rolled_cost_k("base", BOOK, YIELDS, COST)
    assert value_paise(1000, base_k) == 663                      # one pizza base: (₹45 flour + ₹8 yeast) / 8 = ₹6.63
    # a prep inside a prep, nothing made yet: costed through both recipes
    book = dict(BOOK, combo=[Use("sauce", 80_000), Use("base", 1000)])
    y = dict(YIELDS, combo=1000)
    assert value_paise(1000, rolled_cost_k("combo", book, y, COST)) == 663 + 2 * 0 + value_paise(80_000, rolled_cost_k("sauce", BOOK, YIELDS, COST))
    # once a batch has really been made, its real average cost is used instead
    assert rolled_cost_k("combo", book, y, dict(COST, sauce=10_000)) != rolled_cost_k("combo", book, y, COST)
