import pytest

from app.core.cash import DENOMS, clean, total_paise


def test_total():
    counts = {"c1": 10, "c2": 5, "c5": 4, "c10": 3, "c20": 2, "n10": 10, "n20": 5, "n50": 4, "n100": 3, "n200": 2, "n500": 1}
    assert total_paise(counts) == (10 + 10 + 20 + 30 + 40 + 100 + 100 + 200 + 300 + 400 + 500) * 100


def test_clean_drops_zero_and_refuses_junk():
    assert clean({"n500": 2, "c1": 0}) == {"n500": 2}
    assert clean(None) is None
    with pytest.raises(ValueError):
        clean({"n2000": 1})
    with pytest.raises(ValueError):
        clean({"n100": -1})


def test_coins_and_notes_of_same_value_are_separate():
    assert DENOMS["c10"] == DENOMS["n10"] == 10 and total_paise({"c10": 1, "n10": 1}) == 2000


def test_difference_and_tolerance():
    from app.core.cash import describe_difference, difference
    assert difference(100000, 100000) is None
    assert difference(100000, 95000) == -5000 and describe_difference(-5000) == "Rs 50 short"
    assert difference(100000, 100500, tolerance=1000) is None
    assert describe_difference(250) == "Rs 2.50 extra"


# ---------------------------------------------------------------- what the cash was for

import pytest
from pydantic import ValidationError

from app.core import cash_purpose as cp
from app.schemas import CashMoveIn


def test_expense_needs_a_category_and_has_no_limit():
    m = CashMoveIn(kind="out", amount_paise=5_000_000, purpose="expense", category="Milk")
    assert m.category == "Milk" and m.handed is None
    with pytest.raises(ValidationError):
        CashMoveIn(kind="out", amount_paise=100, purpose="expense")


def test_drawing_says_owner_or_bank_and_who():
    m = CashMoveIn(kind="out", amount_paise=500000, purpose="drawing", handed="bank", paid_to="HDFC")
    assert cp.describe("out", m.purpose, m.category, m.handed, m.paid_to, m.reason) == "Drawing: deposited in bank: HDFC"
    m = CashMoveIn(kind="out", amount_paise=500000, purpose="drawing", handed="owner", paid_to="Vineet", reason="for rent")
    assert cp.describe("out", m.purpose, m.category, m.handed, m.paid_to, m.reason) == "Drawing: given to owner: Vineet (for rent)"
    for bad in ({"handed": None, "paid_to": "Vineet"}, {"handed": "owner", "paid_to": " "}, {"handed": "friend", "paid_to": "X"}):
        with pytest.raises(ValidationError):
            CashMoveIn(kind="out", amount_paise=100, purpose="drawing", **bad)


def test_old_clients_still_work_and_in_has_its_own_purposes():
    m = CashMoveIn(kind="out", amount_paise=100, reason="milk purchase")
    assert m.purpose == "other"
    with pytest.raises(ValidationError):
        CashMoveIn(kind="out", amount_paise=100, reason="")
    with pytest.raises(ValidationError):
        CashMoveIn(kind="in", amount_paise=100, purpose="drawing", handed="owner", paid_to="V")
    assert CashMoveIn(kind="in", amount_paise=100, purpose="topup").purpose == "topup"
    assert cp.type_label("in", "change", None) == "Change from bank"
