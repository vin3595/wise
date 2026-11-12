"""SMS settings, message log, resending an e-bill, and the customer's e-bill page."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.permissions import Perm
from ..core.sms import TEMPLATES
from ..db import system_session
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import Bill, SmsMessage, SmsSettings, Tenant
from ..schemas import BillOut, SmsMessageOut, SmsSettingsIn, SmsSettingsOut, SmsTestIn
from ..services import audit
from ..services.billing import bill_out
from ..services.sms import bill_values, enqueue, send_due
from .common import get_or_404

router = APIRouter(tags=["sms"])
IST = ZoneInfo("Asia/Kolkata")


def _out(cfg: SmsSettings | None) -> SmsSettingsOut:
    t = {k: {"label": v["label"], "text": v["text"]} for k, v in TEMPLATES.items()}
    if cfg is None:
        return SmsSettingsOut(enabled=False, provider="log", has_auth_key=False, sender_id=None, template_ids={},
                              send_bill=True, send_ready=True, send_daily=True, send_cash=True, daily_to=[], templates=t)
    return SmsSettingsOut(enabled=cfg.enabled, provider=cfg.provider, has_auth_key=bool(cfg.msg91_auth_key),
                          sender_id=cfg.sender_id, template_ids=cfg.template_ids or {}, send_bill=cfg.send_bill,
                          send_ready=cfg.send_ready, send_daily=cfg.send_daily, send_cash=cfg.send_cash,
                          send_alerts=cfg.send_alerts, alert_discount_bp=cfg.alert_discount_bp,
                          daily_to=cfg.daily_to or [], templates=t)


@router.get("/tenant/sms", response_model=SmsSettingsOut)
def get_sms(auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)):
    return _out(db.get(SmsSettings, auth.tenant_id))


@router.put("/tenant/sms", response_model=SmsSettingsOut)
def put_sms(body: SmsSettingsIn, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)):
    cfg = db.get(SmsSettings, auth.tenant_id)
    if cfg is None:
        cfg = SmsSettings(tenant_id=auth.tenant_id)
        db.add(cfg)
    data = body.model_dump()
    key = data.pop("msg91_auth_key")
    for k, v in data.items():
        setattr(cfg, k, v)
    if key:
        cfg.msg91_auth_key = key.strip()
    if cfg.enabled and cfg.provider == "msg91":
        missing = [k for k, on in (("bill", cfg.send_bill), ("ready", cfg.send_ready), ("daily", cfg.send_daily),
                                   ("cash", cfg.send_cash), ("alert", cfg.send_alerts)) if on and not cfg.template_ids.get(k)]
        if not cfg.msg91_auth_key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter your MSG91 auth key")
        if missing:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"Enter the MSG91 template id for: {', '.join(TEMPLATES[k]['label'] for k in missing)}")
    audit.record(db, auth.tenant_id, auth.user_id, "update", "sms_settings", auth.tenant_id,
                 after={**data, "msg91_auth_key": "(changed)" if key else "(kept)"})
    db.flush()
    return _out(cfg)


@router.post("/tenant/sms/test", response_model=SmsMessageOut)
def test_sms(body: SmsTestIn, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, auth.tenant_id)
    msg = enqueue(db, auth.tenant_id, "test", body.phone, {"brand": tenant.app_name or tenant.name}, force=True)
    if msg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Switch SMS on and save first")
    return msg


@router.get("/sms/messages", response_model=list[SmsMessageOut])
def sms_log(day: date | None = None, status_: str | None = Query(default=None, alias="status", pattern=r"^(queued|sent|failed)$"),
            auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)):
    d = day or datetime.now(IST).date()
    start = datetime.combine(d, time(0), IST).astimezone(timezone.utc)
    stmt = select(SmsMessage).where(SmsMessage.created_at >= start, SmsMessage.created_at < start + timedelta(days=1))
    if status_:
        stmt = stmt.where(SmsMessage.status == status_)
    return list(db.scalars(stmt.order_by(SmsMessage.created_at.desc()).limit(500)))


@router.post("/bills/{bill_id}/sms", response_model=SmsMessageOut)
def resend_bill(bill_id: uuid.UUID, body: SmsTestIn, auth: Auth = Depends(require(Perm.billing)),
                db: Session = Depends(get_db)):
    """Sends the e-bill link (again), e.g. when the customer gives their number after paying."""
    bill = get_or_404(db, Bill, bill_id, "Bill")
    ensure_outlet_access(db, auth, bill.outlet_id)
    if bill.status != "paid":
        raise HTTPException(status.HTTP_409_CONFLICT, "This bill is cancelled")
    msg = enqueue(db, bill.tenant_id, "bill", body.phone, bill_values(db, bill),
                  ref=f"{bill.id}:again:{uuid.uuid4().hex[:8]}", outlet_id=bill.outlet_id, force=True)
    if msg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "SMS is switched off. The owner can switch it on under SMS.")
    audit.record(db, auth.tenant_id, auth.user_id, "sms_bill", "bill", bill.id, after={"to": body.phone})
    return msg


@router.get("/public/bills/{key}", response_model=BillOut)
def public_bill(key: str):
    """The customer's e-bill (link in the SMS). Shows the bill as printed, mobile number hidden."""
    if not 8 <= len(key) <= 16:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    with system_session() as db:
        bill = db.scalar(select(Bill).where(Bill.share_key == key))
        if bill is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
        out = bill_out(db, bill)
    if out.customer_phone:
        out.customer_phone = "XXXXXX" + out.customer_phone[-4:]
    out.cashier_name = None
    out.upi_link = None
    return out


def flush_now() -> None:
    """Used by tests: send due messages immediately."""
    send_due()
