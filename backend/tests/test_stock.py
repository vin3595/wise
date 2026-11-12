import pytest

from app.core.stock import (
    Use, consumption, fmt_qty, margin_bp, parse_qty, recipe_cost, reorder_qty, scale, to_base, unit_cost_k,
    value_paise, variance, weighted_cost,
)


def test_parse_and_format():
    assert parse_qty("12.5") == 12500 and parse_qty("3") == 3000 and parse_qty("0.005") == 5
    for bad in ("", "1.2345", "abc", "1.2.3", "-2"):
        with pytest.raises(ValueError):
            parse_qty(bad)
    assert fmt_qty(12500, "g") == "12.5 g" and fmt_qty(2_500_000, "g") == "2.5 kg" and fmt_qty(3000, "pc") == "3 pc"
    assert fmt_qty(-750_000, "ml") == "-750 ml"


def test_costs():
    kg = to_base(1000, 1000)                       # 1 kg = 1000 g = 1,000,000 milli
    assert kg == 1_000_000
    assert unit_cost_k(45000, kg) == 45000         # ₹450/kg
    assert value_paise(250_000, 45000) == 11250    # 250 g -> ₹112.50
    assert unit_cost_k(54000, to_base(1000, 24)) == 2_250_000  # box of 24 cups at ₹540: ₹22.50 each
    assert value_paise(1000, 2_250_000) == 2250
    with pytest.raises(ValueError):
        unit_cost_k(100, 0)


def test_weighted_cost():
    assert weighted_cost(1_000_000, 40000, 1_000_000, 50000) == 45000
    assert weighted_cost(0, 40000, 500_000, 52000) == 52000
    assert weighted_cost(-200_000, 40000, 500_000, 52000) == 52000   # stock had gone negative
    assert weighted_cost(1_000_000, 40000, 0, 99999) == 40000


def test_recipes():
    milk, sugar, cup = "milk", "sugar", "cup"
    shake = [Use(milk, 250_000), Use(sugar, 20_000), Use(cup, 1000)]
    assert consumption([(2, shake), (1, [Use(milk, 100_000)])]) == {milk: 600_000, sugar: 40_000, cup: 2000}
    costs = {milk: 6000, sugar: 4500, cup: 450_000}     # ₹60/l, ₹45/kg, ₹4.50 a cup
    assert recipe_cost(shake, costs) == 1500 + 90 + 450
    assert scale(250_000, 33333) == 833_325
    assert margin_bp(10000, 2040) == 7960 and margin_bp(0, 10) is None


def test_reorder_and_variance():
    assert reorder_qty(5000, 2000, 0) == 0
    assert reorder_qty(1500, 2000, 0) == 2500
    assert reorder_qty(-500, 2000, 10000) == 10000
    assert reorder_qty(0, 0, 5000) == 0
    assert variance(10_000, 9_200) == -800
