"""Cash differences for the manager to check, and the tolerance setting."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.permissions import Perm
from ..deps import Auth, get_db, require
from ..models import CashAlert, Outlet, Tenant, User, UserOutlet
from ..core.permissions import Role
from ..schemas import CashAlertCheck, CashAlertOut, CashTolerance
from ..services import audit
from .common import get_or_404

router = APIRouter(tags=["cash"])


def _out(db: Session, a: CashAlert, names: dict) -> CashAlertOut:
    outlet = db.get(Outlet, a.outlet_id)
    return CashAlertOut(id=a.id, outlet_name=outlet.name if outlet else "", kind=a.kind,
                        cashier_name=names.get(a.cashier_id), expected_paise=a.expected_paise, counted_paise=a.counted_paise,
                        difference_paise=a.counted_paise - a.expected_paise, denoms=a.denoms, created_at=a.created_at,
                        checked_by_name=names.get(a.checked_by) if a.checked_by else None, checked_at=a.checked_at,
                        note=a.note)


@router.get("/cash-alerts", response_model=list[CashAlertOut])
def list_alerts(open_only: bool = Query(default=True), auth: Auth = Depends(require(Perm.reports_view)),
                db: Session = Depends(get_db)):
    stmt = select(CashAlert)
    if auth.role != Role.owner:
        stmt = stmt.where(CashAlert.outlet_id.in_(select(UserOutlet.outlet_id).where(UserOutlet.user_id == auth.user_id)))
    if open_only:
        stmt = stmt.where(CashAlert.checked_at.is_(None))
    names = {u.id: u.name for u in db.scalars(select(User))}
    return [_out(db, a, names) for a in db.scalars(stmt.order_by(CashAlert.created_at.desc()).limit(200))]


@router.post("/cash-alerts/{alert_id}/check", response_model=CashAlertOut)
def check_alert(alert_id: uuid.UUID, body: CashAlertCheck, auth: Auth = Depends(require(Perm.reports_view)),
                db: Session = Depends(get_db)):
    """The manager looked into it (counted again, found the reason) and notes what happened."""
    a = get_or_404(db, CashAlert, alert_id, "Cash alert")
    if a.checked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already checked")
    a.checked_by, a.checked_at, a.note = auth.user_id, datetime.now(timezone.utc), body.note.strip()
    audit.record(db, auth.tenant_id, auth.user_id, "check", "cash_alert", a.id, after={"note": a.note})
    db.flush()
    return _out(db, a, {u.id: u.name for u in db.scalars(select(User))})


@router.get("/tenant/cash-tolerance", response_model=CashTolerance)
def get_tolerance(auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)):
    return CashTolerance(tolerance_paise=db.get(Tenant, auth.tenant_id).cash_tolerance_paise)


@router.put("/tenant/cash-tolerance", response_model=CashTolerance)
def set_tolerance(body: CashTolerance, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)):
    t = db.get(Tenant, auth.tenant_id)
    before = t.cash_tolerance_paise
    t.cash_tolerance_paise = body.tolerance_paise
    audit.record(db, auth.tenant_id, auth.user_id, "cash_tolerance", "tenant", t.id, {"paise": before}, {"paise": body.tolerance_paise})
    return body
