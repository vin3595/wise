"""Cash counts that don't match: kept for the manager, and sent to managers by SMS."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.cash import describe_difference, difference
from ..core.sms import rupees
from ..models import CashAlert, Outlet, Shift, Tenant, User, UserOutlet
from . import sms


def previous_close(db: Session, outlet_id, drawer_id=None) -> Shift | None:
    """The latest closed shift on this drawer that no later shift has taken over yet."""
    taken = select(Shift.carried_from_id).where(Shift.carried_from_id.is_not(None))
    same = Shift.drawer_id == drawer_id if drawer_id is not None else Shift.drawer_id.is_(None)
    return db.scalar(select(Shift).where(Shift.outlet_id == outlet_id, same, Shift.status == "closed", Shift.id.not_in(taken))
                     .order_by(Shift.closed_at.desc()).limit(1))


def check(db: Session, shift: Shift, kind: str, expected: int, counted: int, denoms: dict | None) -> CashAlert | None:
    from .training import is_training
    if is_training(db, shift.outlet_id):
        return None
    tenant = db.get(Tenant, shift.tenant_id)
    d = difference(expected, counted, tenant.cash_tolerance_paise)
    if d is None:
        return None
    alert = CashAlert(tenant_id=shift.tenant_id, outlet_id=shift.outlet_id, shift_id=shift.id, kind=kind,
                      expected_paise=expected, counted_paise=counted, denoms=denoms,
                      cashier_id=shift.closed_by if kind == "close" and shift.closed_by else shift.opened_by)
    db.add(alert)
    db.flush()
    outlet = db.get(Outlet, shift.outlet_id)
    cashier = db.get(User, alert.cashier_id)
    values = {"outlet": outlet.name, "kind": "at opening" if kind == "open" else "at closing",
              "cashier": cashier.name if cashier else "cashier", "expected": rupees(expected),
              "counted": rupees(counted), "diff": describe_difference(d)}
    for phone in manager_phones(db, shift.outlet_id):
        sms.enqueue(db, shift.tenant_id, "cash", phone, values, ref=f"{alert.id}:{phone}", outlet_id=shift.outlet_id)
    from .alerts import raise_alert
    raise_alert(db, shift.tenant_id, shift.outlet_id, "cash",
                f"Cash {values['kind']} by {values['cashier']} doesn't match: expected Rs {values['expected']}, counted Rs {values['counted']} ({values['diff']})",
                str(alert.id), sms_too=False)   # the cash SMS above already went to the managers
    return alert


def manager_phones(db: Session, outlet_id) -> list[str]:
    """The owner, and managers of this outlet, who have a mobile number."""
    at_outlet = select(UserOutlet.user_id).where(UserOutlet.outlet_id == outlet_id)
    users = db.scalars(select(User).where(User.is_active, User.phone.is_not(None),
                                          or_(User.role == "owner", (User.role == "manager") & User.id.in_(at_outlet))))
    return sorted({u.phone for u in users if u.phone})
