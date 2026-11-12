import pytest

from app.core.offline import Seen, differences, series_for, token_base


def test_series_letters_and_skip_own_code():
    assert series_for("SHKW", set()) == ("A", "SHKA")
    assert series_for("SHKA", set()) == ("B", "SHKB")         # SHKA would equal the main series
    assert series_for("SW", {"A"}) == ("B", "SWB")
    assert series_for("abc", set()) == ("A", "ABCA")
    assert len(series_for("SHKW", set())[1]) <= 4             # CODE/2627/000123 stays within 16


def test_series_runs_out():
    with pytest.raises(ValueError):
        series_for("SHKZ", set("ABCDEFGHIJKLMNOPQRSTUVWXY"))


def test_token_blocks():
    assert token_base("A") == 1000 and token_base("C") == 3000


def test_differences():
    assert differences([Seen("Mango", 12000, 12000, 500, 500)]) == []
    notes = differences([Seen("Mango", 12000, 13000, 500, 500), Seen("Cold coffee", 9000, None, 500, 500),
                         Seen("Fries", 8000, 8000, 500, 1800)])
    assert notes == ["Mango: billed at ₹120, menu price now ₹130", "Cold coffee: no longer on the menu",
                     "Fries: billed with 5% GST, menu now 18%"]
