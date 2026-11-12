"""SMS: putting messages in the outbox, and the worker that sends them.

Messages go into sms_messages inside the same transaction as the bill or order that caused them, so
a bill never goes out without its SMS being recorded, and nothing is sent for a bill that was rolled
back. A background thread sends the outbox every few seconds and retries failures with backoff.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.kds import business_date
from ..core.sms import GIVE_UP_AFTER, msisdn, next_try, render, rupees
from ..db import system_session
from ..models import Bill, Order, Outlet, Payment, SmsMessage, SmsSettings, Tenant

log = logging.getLogger("wise.sms")
IST = ZoneInfo("Asia/Kolkata")
MSG91_FLOW = "https://control.msg91.com/api/v5/flow"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def settings_for(db: Session, tenant_id: uuid.UUID) -> SmsSettings | None:
    s = db.get(SmsSettings, tenant_id)
    return s if s is not None and s.enabled else None


def app_base_url(tenant: Tenant) -> str:
    """Where customers open links: the Wise Box's public address, or the restaurant's own address."""
    s = get_settings()
    if s.customer_url:
        return s.customer_url.rstrip("/")
    if s.base_domain and s.base_domain != "localhost":
        return f"https://{tenant.login_id}.{s.base_domain}"
    return s.public_base_url.rstrip("/")


def enqueue(db: Session, tenant_id: uuid.UUID, kind: str, phone: str, values: dict, *, ref: str | None = None,
            outlet_id: uuid.UUID | None = None, force: bool = False) -> SmsMessage | None:
    """Adds an SMS to the outbox. Returns None if SMS is off for this restaurant/kind, the number isn't
    a mobile, or the same message (kind + ref) was already queued."""
    cfg = db.get(SmsSettings, tenant_id)
    if cfg is None or not cfg.enabled:
        return None
    if not force and not {"bill": cfg.send_bill, "ready": cfg.send_ready, "daily": cfg.send_daily,
                          "cash": cfg.send_cash, "alert": cfg.send_alerts}.get(kind, True):
        return None
    try:
        to = msisdn(phone)[2:]
        body = render(kind, values)
    except ValueError as e:
        log.info("sms %s skipped: %s", kind, e)
        return None
    if ref is not None and db.scalar(select(SmsMessage.id).where(
            SmsMessage.tenant_id == tenant_id, SmsMessage.kind == kind, SmsMessage.ref == ref)) is not None:
        return None
    msg = SmsMessage(tenant_id=tenant_id, outlet_id=outlet_id, kind=kind, to_phone=to, body=body,
                     vars={k: str(v) for k, v in values.items()}, ref=ref, next_try_at=_now())
    db.add(msg)
    db.flush()
    return msg


def ensure_share_key(bill: Bill) -> str:
    if not bill.share_key:
        bill.share_key = secrets.token_urlsafe(9)[:12]
    return bill.share_key


def bill_values(db: Session, bill: Bill) -> dict:
    tenant = db.get(Tenant, bill.tenant_id)
    return {"brand": tenant.app_name or tenant.name, "invoice": bill.invoice_no, "amount": rupees(bill.total_paise),
            "link": f"{app_base_url(tenant)}/b/{ensure_share_key(bill)}"}


def on_bill_created(db: Session, bill: Bill) -> None:
    from .training import is_training
    ensure_share_key(bill)
    if bill.customer_phone and not is_training(db, bill.outlet_id):
        enqueue(db, bill.tenant_id, "bill", bill.customer_phone, bill_values(db, bill), ref=str(bill.id),
                outlet_id=bill.outlet_id)


def on_order_ready(db: Session, order: Order) -> None:
    """'Your order is ready' for takeaway/delivery orders when we have the customer's number."""
    from .training import is_training
    if order.order_type == "dine_in" or not order.token_no or is_training(db, order.outlet_id):
        return
    phone = db.scalar(select(Bill.customer_phone).where(Bill.order_id == order.id, Bill.status == "paid")) \
        or order.customer_phone  # QR / kiosk orders: the number the customer typed
    if not phone:
        return
    tenant = db.get(Tenant, order.tenant_id)
    enqueue(db, order.tenant_id, "ready", phone, {"brand": tenant.app_name or tenant.name, "token": order.token_no},
            ref=str(order.id), outlet_id=order.outlet_id)


def daily_values(db: Session, outlet: Outlet, day) -> dict:
    paid = [Bill.outlet_id == outlet.id, Bill.business_date == day, Bill.status == "paid"]
    n, sales, disc = db.execute(select(func.count(), func.coalesce(func.sum(Bill.total_paise), 0),
                                       func.coalesce(func.sum(Bill.discount_paise), 0)).where(*paid)).one()
    modes = dict(db.execute(select(Payment.mode, func.sum(Payment.amount_paise)).join(Bill, Bill.id == Payment.bill_id)
                            .where(*paid).group_by(Payment.mode)).all())
    cancelled = db.scalar(select(func.count()).select_from(Bill).where(
        Bill.outlet_id == outlet.id, Bill.business_date == day, Bill.status == "cancelled")) or 0
    return {"outlet": outlet.name, "date": day.strftime("%d %b"), "sales": rupees(int(sales)), "bills": int(n),
            "cash": rupees(int(modes.get("cash", 0))), "upi": rupees(int(modes.get("upi", 0))),
            "card": rupees(int(modes.get("card", 0))), "discount": rupees(int(disc)), "cancelled": int(cancelled)}


# ------------------------------------------------------------------ sending

class SendError(Exception):
    pass


def send_one(cfg: SmsSettings, msg: SmsMessage) -> str:
    """Sends through the restaurant's provider. Returns the provider's reference."""
    if cfg.provider == "log":
        log.warning("SMS to %s: %s", msg.to_phone, msg.body)
        return "logged"
    if cfg.provider != "msg91":
        raise SendError(f"Unknown SMS provider {cfg.provider}")
    template = (cfg.template_ids or {}).get(msg.kind)
    if not cfg.msg91_auth_key or not template:
        raise SendError(f"No MSG91 key or template id for {msg.kind} messages")
    try:
        r = httpx.post(MSG91_FLOW, timeout=10, headers={"authkey": cfg.msg91_auth_key, "accept": "application/json"},
                       json={"template_id": template, "short_url": "0",
                             "recipients": [{"mobiles": "91" + msg.to_phone, **msg.vars}]})
    except httpx.HTTPError as e:
        raise SendError(f"Couldn't reach MSG91 ({e.__class__.__name__})") from None
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400 or data.get("type") == "error":
        raise SendError(str(data.get("message") or f"MSG91 answered {r.status_code}")[:300])
    return str(data.get("message") or data.get("request_id") or "sent")[:80]


def send_due(limit: int = 20) -> int:
    """Sends queued messages that are due. Safe to run from several workers at once."""
    done = 0
    with system_session() as db:
        msgs = list(db.scalars(select(SmsMessage).where(SmsMessage.status == "queued", SmsMessage.next_try_at <= _now())
                               .order_by(SmsMessage.created_at).limit(limit).with_for_update(skip_locked=True)))
        for m in msgs:
            cfg = db.get(SmsSettings, m.tenant_id)
            m.attempts += 1
            try:
                if cfg is None or not cfg.enabled:
                    raise SendError("SMS is switched off for this restaurant")
                m.provider_ref = send_one(cfg, m)
                m.status, m.sent_at, m.last_error = "sent", _now(), None
                done += 1
            except SendError as e:
                m.last_error = str(e)
                wait = next_try(m.attempts)
                if wait is None or m.attempts >= GIVE_UP_AFTER:
                    m.status = "failed"
                else:
                    m.next_try_at = _now() + wait
    return done


def queue_daily_summaries() -> int:
    """After the business day ends (4:30 am), each outlet's summary for the day before goes to the
    numbers the owner picked. Runs often; the ref stops repeats."""
    now_ist = datetime.now(IST)
    if now_ist.time() < dtime(4, 30):
        return 0
    day = business_date() - timedelta(days=1)
    n = 0
    with system_session() as db:
        # System session: every query below filters by the restaurant's own outlet explicitly.
        for cfg in list(db.scalars(select(SmsSettings).where(SmsSettings.enabled, SmsSettings.send_daily))):
            if not cfg.daily_to:
                continue
            for outlet in list(db.scalars(select(Outlet).where(Outlet.tenant_id == cfg.tenant_id, Outlet.is_active,
                                                                Outlet.is_training.is_(False)))):
                values = daily_values(db, outlet, day)
                if values["bills"] == 0:
                    continue
                for phone in cfg.daily_to:
                    if enqueue(db, cfg.tenant_id, "daily", phone, values, ref=f"{outlet.id}:{day}:{phone}",
                               outlet_id=outlet.id):
                        n += 1
    return n


_started = False


def start_worker(interval: float = 5.0) -> None:
    """Background sender inside the API process (one per process; SKIP LOCKED keeps them apart)."""
    global _started
    if _started:
        return
    _started = True

    def loop() -> None:
        last_daily = 0.0
        while True:
            try:
                send_due()
                if time.monotonic() - last_daily > 600:
                    queue_daily_summaries()
                    last_daily = time.monotonic()
                from .alerts import check_printers, heartbeat
                heartbeat()
                check_printers()
            except Exception:  # noqa: BLE001  (database down, etc.: try again next round)
                log.exception("sms worker round failed")
            time.sleep(interval)

    threading.Thread(target=loop, name="wise-sms", daemon=True).start()
