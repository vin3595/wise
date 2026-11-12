from datetime import datetime, timedelta, timezone

import pytest

from app.core.kds import (
    AreaKey, TransitionError, business_date, car_matches, check_transition, normalize_car_number, order_status,
    split_by_area, ticket_age_level,
)
from app.core.permissions import Perm, Role, has_perm


@pytest.mark.parametrize("cur,target", [("new", "preparing"), ("preparing", "ready"), ("ready", "served"),
                                        ("new", "ready"), ("preparing", "new"), ("ready", "preparing")])
def test_allowed_transitions(cur, target):
    check_transition(cur, target)


@pytest.mark.parametrize("cur,target", [("new", "served"), ("served", "ready"), ("served", "new"),
                                        ("ready", "new"), ("new", "new")])
def test_blocked_transitions(cur, target):
    with pytest.raises(TransitionError):
        check_transition(cur, target)


def test_order_status_from_tickets():
    assert order_status([]) == "new"
    assert order_status(["new", "new"]) == "new"
    assert order_status(["new", "preparing"]) == "preparing"
    assert order_status(["ready", "new"]) == "preparing"       # shakes ready, fries not started
    assert order_status(["ready", "ready"]) == "ready"
    assert order_status(["ready", "served"]) == "ready"
    assert order_status(["served", "served"]) == "completed"


def test_split_by_area_keeps_order_and_default():
    shake, kitchen = AreaKey("a1", "Shake counter"), AreaKey("a2", "Kitchen")
    lines = [("oreo", shake), ("fries", kitchen), ("cold coffee", shake), ("mystery", None)]
    groups = split_by_area(lines, lambda line: line[1])
    assert [(k.name, [x[0] for x in v]) for k, v in groups] == [
        ("Shake counter", ["oreo", "cold coffee"]), ("Kitchen", ["fries"]), ("Kitchen", ["mystery"])]
    assert groups[2][0].id is None  # unassigned items get their own default ticket


def test_business_date_rolls_at_4am_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert str(business_date(datetime(2026, 11, 10, 23, 30, tzinfo=ist))) == "2026-11-10"
    assert str(business_date(datetime(2026, 11, 11, 1, 30, tzinfo=ist))) == "2026-11-10"   # late night
    assert str(business_date(datetime(2026, 11, 11, 4, 0, tzinfo=ist))) == "2026-11-11"
    assert str(business_date(datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc))) == "2026-11-10"  # 01:30 IST


def test_ticket_age_level():
    t0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    assert ticket_age_level(t0, t0 + timedelta(minutes=3)) == "ok"
    assert ticket_age_level(t0, t0 + timedelta(minutes=9)) == "warn"
    assert ticket_age_level(t0, t0 + timedelta(minutes=16)) == "late"


@pytest.mark.parametrize("raw,out", [("rj 19 ca 1234", "RJ19CA1234"), ("RJ-19-CA-1234", "RJ19CA1234"),
                                     ("1234", "1234"), ("", None), (None, None)])
def test_car_number(raw, out):
    assert normalize_car_number(raw) == out


@pytest.mark.parametrize("raw", ["12", "RJ19CA12345678", "RJ19@1234"])
def test_car_number_bad(raw):
    with pytest.raises(ValueError):
        normalize_car_number(raw)


def test_car_search():
    assert car_matches("RJ19CA1234", "1234")
    assert car_matches("RJ19CA1234", "ca 12")
    assert not car_matches("RJ19CA1234", "9999")
    assert not car_matches(None, "1234")
    assert not car_matches("RJ19CA1234", "")


def test_kitchen_permissions():
    assert has_perm(Role.kitchen, Perm.kds_update)
    assert not has_perm(Role.kitchen, Perm.kot_send)
    assert has_perm(Role.captain, Perm.kot_send) and has_perm(Role.captain, Perm.kds_view)
    assert not has_perm(Role.captain, Perm.kds_update)
    assert has_perm(Role.cashier, Perm.order_complete)
