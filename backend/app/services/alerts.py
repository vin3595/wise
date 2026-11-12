"""Owner alerts: things to know at once, in Wise (Home) and by SMS to the owner's numbers.

Cancelled bills, big discounts, complimentary bills, bill corrections, a drawer that doesn't match,
the KOT printer not printing, and the box having been off. Training outlets never alert.
Each alert has a ref so the same event never alerts twice."""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..core.sms import rupees
from ..models import Bill, Kot, Outlet, OwnerAlert, SmsSettings, User
from . import sms
from .training import is_training

log = logging.getLogger("wise.alerts")
IST = ZoneInfo("Asia/Kolkata")
PRINTER_LATE = timedelta(minutes=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def recipients(db: Session, tenant_id: uuid.UUID) -> list[str]:
    """The numbers picked for the daily summary; if none, the owner's own mobile."""
    cfg = db.get(SmsSettings, tenant_id)
    if cfg and cfg.daily_to:
        return list(cfg.daily_to)
    return sorted({u.phone for u in db.scalars(select(User).where(User.tenant_id == tenant_id, User.role == "owner",
                                                                    User.is_active, User.phone.is_not(None))) if u.phone})


def raise_alert(db: Session, tenant_id: uuid.UUID, outlet_id: uuid.UUID | None, kind: str, text: str, ref: str,
                sms_too: bool = True) -> bool:
    """Records the alert (once per kind+ref) and queues the SMS. Returns False if it was already raised."""
    if outlet_id is not None and is_training(db, outlet_id):
        return False
    row = db.execute(insert(OwnerAlert).values(id=uuid.uuid4(), tenant_id=tenant_id, outlet_id=outlet_id, kind=kind,
                                                text=text[:300], ref=ref[:80])
                     .on_conflict_do_nothing(index_elements=[OwnerAlert.tenant_id, OwnerAlert.kind, OwnerAlert.ref])
                     .returning(OwnerAlert.id)).first()
    if row is None:
        return False
    if not sms_too:
        return True
    outlet = db.get(Outlet, outlet_id) if outlet_id else None
    for phone in recipients(db, tenant_id):
        sms.enqueue(db, tenant_id, "alert", phone, {"outlet": outlet.name if outlet else "Wise", "what": text[:160]},
                    ref=f"{kind}:{ref}:{phone}"[:80], outlet_id=outlet_id)
    return True


def _who(db: Session, uid: uuid.UUID | None) -> str:
    u = db.get(User, uid) if uid else None
    return u.name if u else "someone"


def on_bill_created(db: Session, bill: Bill, modes: list[str]) -> None:
    if "complimentary" in modes:
        raise_alert(db, bill.tenant_id, bill.outlet_id, "comp",
                    f"Complimentary bill {bill.invoice_no} worth Rs {rupees(bill.gross_paise)} by {_who(db, bill.created_by)}"
                    + (f", approved by {_who(db, bill.approved_by)}" if bill.approved_by and bill.approved_by != bill.created_by else ""),
                    str(bill.id))
        return
    if not bill.discount_paise or not bill.gross_paise or bill.loyalty_points_paise or bill.loyalty_reward_paise:
        return
    cfg = db.get(SmsSettings, bill.tenant_id)
    limit = cfg.alert_discount_bp if cfg else 2000
    pct = bill.discount_paise * 10000 // bill.gross_paise
    if pct >= limit:
        raise_alert(db, bill.tenant_id, bill.outlet_id, "discount",
                    f"{pct / 100:.0f}% discount (Rs {rupees(bill.discount_paise)}) on bill {bill.invoice_no} by {_who(db, bill.created_by)}: "
                    f"{bill.discount_reason or 'no reason'}", str(bill.id))


def on_bill_cancelled(db: Session, bill: Bill) -> None:
    raise_alert(db, bill.tenant_id, bill.outlet_id, "cancel",
                f"Bill {bill.invoice_no} of Rs {rupees(bill.total_paise)} cancelled by {_who(db, bill.cancelled_by)}"
                f" (approved by {_who(db, bill.cancel_approved_by)}): {bill.cancel_reason or ''}", str(bill.id))


def on_bill_corrected(db: Session, bill: Bill, before_paise: int, user_id: uuid.UUID | None, reason: str) -> None:
    raise_alert(db, bill.tenant_id, bill.outlet_id, "edit",
                f"Bill {bill.invoice_no} corrected from Rs {rupees(before_paise)} to Rs {rupees(bill.total_paise)} by {_who(db, user_id)}: {reason}",
                f"{bill.id}:{bill.revision}")


# ------------------------------------------------------------------ checks the worker runs

def check_printers() -> int:
    """A KOT still unprinted two minutes after it was sent: the print station or printer is off."""
    from ..db import system_session
    n = 0
    with system_session() as db:
        cutoff = _now() - PRINTER_LATE
        late = db.execute(select(Kot.outlet_id, Kot.tenant_id).join(Outlet, Outlet.id == Kot.outlet_id)
                          .where(Outlet.print_kots, Outlet.is_active, Outlet.is_training.is_(False), Kot.printed_at.is_(None),
                                 Kot.status != "cancelled", Kot.created_at < cutoff, Kot.created_at > cutoff - timedelta(hours=2),
                                 (Outlet.print_since.is_(None)) | (Kot.created_at > Outlet.print_since))
                          .group_by(Kot.outlet_id, Kot.tenant_id)).all()
        hour = datetime.now(IST).strftime("%Y%m%d%H")
        for outlet_id, tenant_id in late:
            if raise_alert(db, tenant_id, outlet_id, "printer",
                           "Kitchen tickets are not printing. Check the KOT printer and the print station PC.", f"{outlet_id}:{hour}"):
                n += 1
    return n


def _beat_file() -> Path:
    from ..config import get_settings
    return Path(get_settings().upload_dir) / ".wise-heartbeat"


_last_beat = 0.0


def heartbeat() -> None:
    """Touches a file every minute; on start-up a stale file means the box was off (see box_was_off)."""
    global _last_beat
    if time.monotonic() - _last_beat < 60:
        return
    _last_beat = time.monotonic()
    try:
        f = _beat_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
    except OSError:
        pass


def box_was_off(min_gap: timedelta = timedelta(minutes=5)) -> int:
    """Called once at start-up. If Wise was down for more than a few minutes, every restaurant on this box
    hears when it went off and when it came back (an alert can't be sent while the box itself is off)."""
    from ..db import system_session
    from ..models import Tenant
    try:
        last = datetime.fromtimestamp(os.path.getmtime(_beat_file()), timezone.utc)
    except OSError:
        return 0
    gap = _now() - last
    if gap < min_gap:
        return 0
    text = (f"Wise was off from {last.astimezone(IST):%d %b %H:%M} to {_now().astimezone(IST):%H:%M}. "
            "Check the box's power and network if you didn't expect this.")
    n = 0
    with system_session() as db:
        for t in db.scalars(select(Tenant.id)):
            if raise_alert(db, t, None, "box", text, f"{last:%Y%m%d%H%M}"):
                n += 1
    return n
