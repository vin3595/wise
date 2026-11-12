"""Correcting wrong entries on issued bills: one bill or many, saved together."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.permissions import Perm, Role
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import Bill, BillRevision, Tenant, User
from ..schemas import BillEditBatch, BillEditBatchOut, BillFixResult, BillLockIn, BillRevisionOut
from ..services import audit
from ..services.approval import verify_manager_pin
from ..services.bill_edit import FixError, apply_plan, plan_fix
from .common import get_or_404

router = APIRouter(tags=["bill edits"])


@router.post("/bills/corrections", response_model=BillEditBatchOut)
def correct_bills(body: BillEditBatch, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    """Checks every bill first; saves all of them only if every one is fine (otherwise saves nothing and
    says what's wrong with which bill). `dry_run` shows the new totals without saving."""
    is_manager = auth.can(Perm.approve)
    tenant = db.get(Tenant, auth.tenant_id)
    if len({f.bill_id for f in body.bills}) != len(body.bills):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A bill is in the list twice")
    bills = list(db.scalars(select(Bill).where(Bill.id.in_([f.bill_id for f in body.bills])).with_for_update()))
    outlets = {b.outlet_id for b in bills}
    if len(outlets) > 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Correct one outlet's bills at a time")
    for o in outlets:
        ensure_outlet_access(db, auth, o)

    plans, results = [], []
    for fix in body.bills:
        try:
            p = plan_fix(db, tenant, fix, is_manager=is_manager, user_id=auth.user_id)
            plans.append(p)
            results.append(p.result)
        except FixError as e:
            b = db.get(Bill, fix.bill_id)
            results.append(BillFixResult(bill_id=fix.bill_id, invoice_no=b.invoice_no if b else "?", ok=False,
                                         error=str(e)))
    all_ok = all(r.ok for r in results)
    needs_pin = any(r.needs_approval for r in results)
    if body.dry_run or not all_ok:
        return BillEditBatchOut(saved=False, results=results, needs_approval=needs_pin)

    approver = None
    if any(p.approvals for p in plans):
        approver = auth.user_id if is_manager else verify_manager_pin(
            db, auth.tenant_id, next(iter(outlets)), body.manager_pin).id
    reason = body.reason.strip()
    for p in plans:
        before = p.bill.total_paise
        apply_plan(db, p, user_id=auth.user_id, approver=approver, reason=reason)
        audit.record(db, auth.tenant_id, auth.user_id, "correct", "bill", p.bill.id,
                     {"total_paise": before, "revision": p.bill.revision - 1},
                     {"total_paise": p.bill.total_paise, "changes": p.result.changes, "reason": reason,
                      "approved_by": str(approver) if approver else None})
    return BillEditBatchOut(saved=True, results=results, needs_approval=needs_pin)


@router.get("/bills/{bill_id}/history", response_model=list[BillRevisionOut])
def bill_history(bill_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    """Every earlier version of a bill, oldest first."""
    bill = get_or_404(db, Bill, bill_id, "Bill")
    ensure_outlet_access(db, auth, bill.outlet_id)
    names = {u.id: u.name for u in db.scalars(select(User))}
    return [BillRevisionOut(revision_no=r.revision_no, created_at=r.created_at, edited_by_name=names.get(r.edited_by),
                            approved_by_name=names.get(r.approved_by) if r.approved_by else None, reason=r.reason,
                            changes=r.changes, old_total_paise=r.old_total_paise, new_total_paise=r.new_total_paise,
                            before=r.snapshot)
            for r in db.scalars(select(BillRevision).where(BillRevision.bill_id == bill.id)
                                .order_by(BillRevision.revision_no))]


@router.get("/tenant/bill-lock", response_model=BillLockIn)
def get_bill_lock(auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    return BillLockIn(through=db.get(Tenant, auth.tenant_id).bills_locked_through)


@router.put("/tenant/bill-lock", response_model=BillLockIn)
def set_bill_lock(body: BillLockIn, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)):
    """After the GST return for a period is filed, lock its bills so they can't be corrected."""
    tenant = db.get(Tenant, auth.tenant_id)
    before = tenant.bills_locked_through
    if body.through is not None and body.through > date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The lock date can't be in the future")
    tenant.bills_locked_through = body.through
    audit.record(db, auth.tenant_id, auth.user_id, "bill_lock", "tenant", tenant.id,
                 {"through": str(before) if before else None}, {"through": str(body.through) if body.through else None})
    return BillLockIn(through=tenant.bills_locked_through)
