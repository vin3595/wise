from datetime import date

from app.core.bill_edit import EditFacts, approval_needed, blockers, describe

D = date(2026, 11, 10)


def facts(**kw):
    base = dict(items_changed=False, payments_changed=False, customer_changed=True, old_total_paise=10000,
                new_total_paise=10000, bill_date=D, today=D, shift_open=True, is_manager=False, locked_through=None)
    return EditFacts(**{**base, **kw})


def test_customer_fix_needs_no_approval():
    f = facts()
    assert blockers(f) is None and approval_needed(f) == [] and describe(f) == ["customer"]


def test_payment_mode_fix_on_open_shift():
    f = facts(customer_changed=False, payments_changed=True)
    assert blockers(f) is None and approval_needed(f) == []


def test_item_change_needs_manager():
    f = facts(customer_changed=False, items_changed=True, payments_changed=True, new_total_paise=8000)
    assert approval_needed(f) == ["Items or discount changed", "Bill total goes down"]
    assert describe(f) == ["items", "payment"]


def test_nothing_changed():
    assert blockers(facts(customer_changed=False)) == "Nothing was changed"


def test_cashier_limits():
    assert "earlier day" in blockers(facts(bill_date=date(2026, 11, 9)))
    assert "closed" in blockers(facts(shift_open=False, payments_changed=True))
    assert blockers(facts(shift_open=False)) is None  # customer details are fine after close
    assert blockers(facts(shift_open=False, payments_changed=True, bill_date=date(2026, 10, 2), is_manager=True)) is None


def test_lock_date_wins_for_everyone():
    f = facts(is_manager=True, bill_date=date(2026, 10, 31), locked_through=date(2026, 10, 31))
    assert "locked" in blockers(f)
    assert blockers(facts(is_manager=True, bill_date=date(2026, 11, 1), locked_through=date(2026, 10, 31))) is None
