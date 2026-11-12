"""UPI that confirms itself (Razorpay QR), so the cashier never has to check a phone.

The counter asks for a QR for the bill amount; the customer pays; the bill is made by itself with the UPI
reference as soon as Razorpay says the money came in, by webhook or by the box asking while the counter
waits. Provider "test" makes a pretend QR with a "customer paid" button, for training without real money.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import paytm as pt
from ..core import razorpay as rz
from ..core import razorpay_pos as rp
from ..core.permissions import Perm, Role
from ..db import system_session, tenant_session
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import Bill, CardRequest, CardTerminal, Order, Outlet, PaymentSettings, Tenant, UpiRequest, User
from ..schemas import BillCreate, CustomerIn, LoyaltyUseIn, PaymentLineIn
from ..services import audit
from ..services.billing import bill_out, billable_rows, preview
from ..services.sms import app_base_url
from ..core.billing import compute_bill
from .common import get_or_404

router = APIRouter(tags=["payments"])
QR_MINUTES = 15
POLL_SECONDS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ settings

class PaySettingsIn(BaseModel):
    enabled: bool
    provider: str = Field(pattern=r"^(razorpay|paytm|test)$")
    pos_enabled: bool = False
    pos_provider: str = Field(default="test", pattern=r"^(razorpay_pos|test)$")
    pos_live: bool = False
    pos_username: str | None = Field(default=None, max_length=40)
    pos_app_key: str | None = Field(default=None, max_length=80)      # empty = keep the saved one
    key_id: str | None = Field(default=None, max_length=40)
    key_secret: str | None = Field(default=None, max_length=80)      # empty = keep the saved one
    webhook_secret: str | None = Field(default=None, max_length=80)
    paytm_mid: str | None = Field(default=None, max_length=40)
    paytm_key: str | None = Field(default=None, max_length=40)        # empty = keep the saved one
    paytm_client_id: str | None = Field(default=None, max_length=8)
    paytm_live: bool = False
    qr_minutes: int = Field(default=10, ge=2, le=1440)
    print_qr: bool = False


def _settings(db: Session, tenant_id: uuid.UUID) -> PaymentSettings:
    cfg = db.get(PaymentSettings, tenant_id)
    if cfg is None:
        cfg = PaymentSettings(tenant_id=tenant_id)
        db.add(cfg)
        db.flush()
    return cfg


def _settings_out(db: Session, cfg: PaymentSettings) -> dict:
    tenant = db.get(Tenant, cfg.tenant_id)
    return {"enabled": cfg.enabled, "provider": cfg.provider, "key_id": cfg.key_id, "has_key_secret": bool(cfg.key_secret),
            "has_webhook_secret": bool(cfg.webhook_secret),
            "webhook_url": f"{app_base_url(tenant)}/api/public/razorpay/{cfg.webhook_key}", "webhook_event": "qr_code.credited",
            "pos_enabled": cfg.pos_enabled, "pos_provider": cfg.pos_provider, "pos_live": cfg.pos_live, "pos_username": cfg.pos_username,
            "has_pos_app_key": bool(cfg.pos_app_key),
            "paytm_mid": cfg.paytm_mid, "has_paytm_key": bool(cfg.paytm_key), "paytm_client_id": cfg.paytm_client_id,
            "paytm_live": cfg.paytm_live, "paytm_callback_url": f"{app_base_url(tenant)}/api/public/paytm/{cfg.webhook_key}",
            "qr_minutes": cfg.qr_minutes, "print_qr": cfg.print_qr}


@router.get("/tenant/payments")
def get_pay_settings(auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> dict:
    return _settings_out(db, _settings(db, auth.tenant_id))


@router.put("/tenant/payments")
def put_pay_settings(body: PaySettingsIn, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> dict:
    cfg = _settings(db, auth.tenant_id)
    cfg.enabled, cfg.provider = body.enabled, body.provider
    if body.key_id is not None:
        cfg.key_id = body.key_id.strip() or None
    if body.key_secret:
        cfg.key_secret = body.key_secret.strip()
    if body.webhook_secret:
        cfg.webhook_secret = body.webhook_secret.strip()
    if body.paytm_mid is not None:
        cfg.paytm_mid = body.paytm_mid.strip() or None
    if body.paytm_key:
        cfg.paytm_key = body.paytm_key.strip()
    if body.paytm_client_id is not None:
        cfg.paytm_client_id = body.paytm_client_id.strip() or None
    cfg.paytm_live, cfg.qr_minutes, cfg.print_qr = body.paytm_live, body.qr_minutes, body.print_qr
    if cfg.enabled and cfg.provider == "paytm":
        if not (cfg.paytm_mid and cfg.paytm_key):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the Paytm MID and merchant key (Paytm for Business dashboard > Developer settings > API keys)")
        if len(cfg.paytm_key.encode()) not in (16, 24, 32):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The Paytm merchant key should be 16 characters. Copy it again from the Paytm dashboard.")
    cfg.pos_enabled, cfg.pos_provider, cfg.pos_live = body.pos_enabled, body.pos_provider, body.pos_live
    if body.pos_username is not None:
        cfg.pos_username = body.pos_username.strip() or None
    if body.pos_app_key:
        cfg.pos_app_key = body.pos_app_key.strip()
    if cfg.pos_enabled and cfg.pos_provider == "razorpay_pos" and not (cfg.pos_username and cfg.pos_app_key):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the Razorpay POS username and app key (Razorpay POS gives these for billing software)")
    if cfg.enabled and cfg.provider == "razorpay" and not (cfg.key_id and cfg.key_secret):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the Razorpay Key ID and Key Secret (Dashboard > Account & Settings > API Keys)")
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "payment_settings", None,
                 after={"enabled": cfg.enabled, "provider": cfg.provider, "key_id": cfg.key_id,
                        "key_secret": "(changed)" if body.key_secret else "(kept)", "paytm_mid": cfg.paytm_mid,
                        "paytm_key": "(changed)" if body.paytm_key else "(kept)", "paytm_live": cfg.paytm_live})
    return _settings_out(db, cfg)


@router.post("/tenant/payments/test-qr")
def test_qr(auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> dict:
    """A real ₹1 Paytm QR, not tied to any order or bill: checks the MID, key, server and status in one go.
    The rupee lands in your own Paytm account (refund it from the Paytm dashboard if you like)."""
    cfg = _settings(db, auth.tenant_id)
    if cfg.provider != "paytm" or not (cfg.paytm_mid and cfg.paytm_key):
        raise HTTPException(status.HTTP_409_CONFLICT, "Pick Paytm Dynamic QR and save the MID and merchant key first")
    oid = "WT" + uuid.uuid4().hex[:24]
    expires = _now() + timedelta(minutes=cfg.qr_minutes or 10)
    try:
        body = pt.qr_body(cfg.paytm_mid, oid, 100, pt.pos_id("TEST"), expires, "Wise test", "Wise test payment")
        data, _qr = pt.qr_from_reply(_paytm_call(cfg, pt.QR_CREATE, body))
    except (pt.PaytmError, ValueError) as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from None
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Couldn't reach Paytm ({e.__class__.__name__}). Check the box's internet, and the test/live server switch.") from None
    audit.record(db, auth.tenant_id, auth.user_id, "create", "payment_test_qr", None, after={"order_id": oid, "live": cfg.paytm_live})
    return {"order_id": oid, "upi_string": data, "amount_paise": 100, "expires_at": expires.isoformat(), "live": cfg.paytm_live}


@router.get("/tenant/payments/test-qr/{oid}")
def test_qr_status(oid: str, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> dict:
    cfg = _settings(db, auth.tenant_id)
    if not oid.startswith("WT") or len(oid) > 40 or not (cfg.paytm_mid and cfg.paytm_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        st = pt.read_status(_paytm_call(cfg, pt.STATUS, pt.status_body(cfg.paytm_mid, oid)))
    except Exception as e:
        return {"state": "unknown", "message": f"Couldn't reach Paytm ({e.__class__.__name__})"}
    return {"state": st.state, "message": st.message, "reference": st.paid.payment_id if st.paid else None,
            "amount_paise": st.paid.amount_paise if st.paid else None}


# ------------------------------------------------------------------ talking to Razorpay

class GatewayError(Exception):
    pass


def _client(cfg: PaymentSettings):
    import httpx
    return httpx.Client(base_url=rz.API, auth=(cfg.key_id or "", cfg.key_secret or ""), timeout=10)


def _paytm_call(cfg: PaymentSettings, path: str, body: dict) -> dict:
    import httpx
    payload = pt.signed(body, cfg.paytm_key or "", cfg.paytm_client_id)
    r = httpx.post(f"{pt.LIVE if cfg.paytm_live else pt.STAGE}{path}", content=payload.encode(),
                   headers={"Content-Type": "application/json"}, timeout=10)
    try:
        return r.json()
    except ValueError:
        return {"body": {"resultInfo": {"resultStatus": "FAILURE", "resultMsg": f"Paytm said {r.status_code}"}}}


def _paytm_status(cfg: PaymentSettings, req: UpiRequest) -> pt.Status:
    if not req.provider_ref:
        return pt.Status("unknown")
    try:
        return pt.read_status(_paytm_call(cfg, pt.STATUS, pt.status_body(cfg.paytm_mid or "", req.provider_ref)))
    except Exception:
        return pt.Status("unknown")


def _create_qr(cfg: PaymentSettings, req: UpiRequest, outlet: Outlet, token: int | None) -> None:
    if req.provider == "paytm":
        oid = pt.order_id(req.id.hex)
        try:
            body = pt.qr_body(cfg.paytm_mid or "", oid, req.amount_paise, pt.pos_id(outlet.code), req.expires_at,
                              outlet.name, f"Token {token}" if token else "Bill")
            data, _qr_id = pt.qr_from_reply(_paytm_call(cfg, pt.QR_CREATE, body))
        except (pt.PaytmError, ValueError) as e:
            raise GatewayError(str(e)) from None
        except Exception as e:  # network down, timeout
            raise GatewayError(f"Couldn't reach Paytm ({e.__class__.__name__}). Use the outlet's own UPI QR and tap UPI received.") from None
        req.provider_ref, req.upi_string = oid, data
        return
    if req.provider == "test":
        req.provider_ref = f"test_{uuid.uuid4().hex[:14]}"
        req.upi_string = (f"upi://pay?pa=test.wise@razorpay&pn={quote(outlet.name)}&am={req.amount_paise / 100:.2f}"
                          f"&cu=INR&tn={quote('Test only - do not pay')}")
        return
    body = rz.qr_request(req.amount_paise, f"{outlet.name} counter", f"Token {token}" if token else "Bill",
                         int(req.expires_at.timestamp()), {"wise_request": str(req.id), "order": str(req.order_id)})
    try:
        with _client(cfg) as c:
            r = c.post("/payments/qr_codes", json=body)
        if r.status_code >= 400:
            msg = (r.json().get("error") or {}).get("description") if r.headers.get("content-type", "").startswith("application/json") else None
            raise GatewayError(msg or f"Razorpay said {r.status_code}")
        data = r.json()
    except GatewayError:
        raise
    except Exception as e:  # network down, bad keys, timeout
        raise GatewayError(f"Couldn't reach Razorpay ({e.__class__.__name__}). Use the outlet's own UPI QR and tap UPI received.") from None
    req.provider_ref = data.get("id")
    req.upi_string = data.get("image_content") or None
    if not req.upi_string:
        raise GatewayError("Razorpay didn't return a UPI code. Check that UPI QR is activated on your Razorpay account.")


def _fetch_paid(cfg: PaymentSettings, req: UpiRequest) -> rz.Paid | None:
    if req.provider == "paytm":
        st = _paytm_status(cfg, req)
        if st.state == "failed" and req.status == "waiting":
            req.problem = st.message      # shown to the cashier; the QR stays open for another try
        return st.paid
    if req.provider != "razorpay" or not req.provider_ref:
        return None
    try:
        with _client(cfg) as c:
            r = c.get(f"/payments/qr_codes/{req.provider_ref}/payments")
        if r.status_code >= 400:
            return None
        return rz.first_paid(r.json().get("items") or [])
    except Exception:
        return None


def _close_qr(cfg: PaymentSettings, req: UpiRequest) -> None:
    # Paytm's dynamic QR has no close call: it stops working at its expiry time.
    if req.provider != "razorpay" or not req.provider_ref:
        return
    try:
        with _client(cfg) as c:
            c.post(f"/payments/qr_codes/{req.provider_ref}/close")
    except Exception:
        pass


# ------------------------------------------------------------------ settling a paid request

def settle(db: Session, req: UpiRequest, paid: rz.Paid) -> None:
    """Money came in: make the bill (once). Problems are kept on the request for the cashier to see."""
    if req.status not in ("waiting", "closed"):
        return
    req.payment_ref, req.payer, req.paid_at = paid.payment_id, paid.payer, _now()
    problem = rz.mismatch(req.amount_paise, paid)
    if problem:
        req.status, req.problem = "problem", problem
        return
    make_bill(db, req, "upi", paid.payment_id)


def make_bill(db: Session, req, mode: str, reference: str | None) -> None:
    """The money is in (UPI or card): make the bill the cashier started, with the payment's reference."""
    from .billing import issue_bill
    order = db.get(Order, req.order_id)
    user = db.get(User, req.created_by)
    if order is None or user is None:
        req.status, req.problem = "problem", "The order or cashier is gone"
        return
    cashier = Auth(user_id=user.id, tenant_id=req.tenant_id, role=Role(user.role), outlet_id=req.outlet_id, via="pin")
    b = req.bill_body
    body = BillCreate(id=uuid.UUID(b["bill_id"]), order_id=order.id, customer=CustomerIn(**b["customer"]),
                      loyalty=LoyaltyUseIn(**b["loyalty"]) if b.get("loyalty") else None,
                      payments=[PaymentLineIn(mode=mode, amount_paise=req.amount_paise, reference=reference)])
    try:
        with db.begin_nested():
            bill = issue_bill(db, cashier, order, body)
    except HTTPException as e:
        req.status, req.problem = "problem", f"Paid, but the bill couldn't be made: {e.detail}"
        return
    req.status, req.bill_id = "billed", bill.id


def _out(db: Session, req: UpiRequest) -> dict:
    cfg = db.get(PaymentSettings, req.tenant_id)
    out: dict = {"id": str(req.id), "status": req.status, "amount_paise": req.amount_paise, "upi_string": req.upi_string,
                 "provider": req.provider, "expires_at": req.expires_at.isoformat(), "payment_ref": req.payment_ref,
                 "payer": req.payer, "problem": req.problem, "order_id": str(req.order_id), "bill": None,
                 "slip": (req.bill_body or {}).get("slip"), "print_qr": bool(cfg and cfg.print_qr)}
    if req.bill_id:
        out["bill"] = bill_out(db, db.get(Bill, req.bill_id)).model_dump(mode="json")
    return out


# ------------------------------------------------------------------ counter endpoints

@router.get("/upi/enabled")
def upi_enabled(auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    cfg = db.get(PaymentSettings, auth.tenant_id)
    return {"enabled": bool(cfg and cfg.enabled), "provider": cfg.provider if cfg else None,
            "card_enabled": bool(cfg and cfg.pos_enabled), "card_provider": cfg.pos_provider if cfg else None}


class UpiIn(BaseModel):
    order_id: uuid.UUID
    bill_id: uuid.UUID                    # the bill's id, made on the device (safe retries)
    customer: CustomerIn = CustomerIn()
    loyalty: LoyaltyUseIn | None = None


@router.post("/upi/requests")
def new_upi_request(body: UpiIn, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    cfg = db.get(PaymentSettings, auth.tenant_id)
    if cfg is None or not cfg.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "UPI that confirms itself isn't switched on. Use the outlet's UPI QR.")
    order = get_or_404(db, Order, body.order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if db.scalar(select(Bill.id).where(Bill.order_id == order.id, Bill.status == "paid")):
        raise HTTPException(status.HTTP_409_CONFLICT, "This order is already billed")
    c = body.customer
    if not c.phone and not c.declined:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ask for the customer's mobile number, or tap No number")
    # One live QR per order: an older one still waiting is closed first.
    for old in db.scalars(select(UpiRequest).where(UpiRequest.order_id == order.id, UpiRequest.status == "waiting").with_for_update()):
        _close_qr(cfg, old)
        old.status = "closed"
    rows = billable_rows(db, order)
    if not rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing left to bill on this order")
    from .billing import _loyalty
    disc, _lp, _lr, _why = _loyalty(db, auth.tenant_id, body.loyalty, None, c.phone, rows, order.outlet_id)
    calc = compute_bill([r.line for r in rows], disc)
    total = calc.total_paise
    outlet = db.get(Outlet, order.outlet_id)
    # What's printed on the QR slip: the bill as it will be made (the invoice number comes when it's paid).
    slip = preview(rows, calc, []).model_dump(mode="json")
    tenant = db.get(Tenant, auth.tenant_id)
    slip.update({"outlet": {"name": outlet.name, "legal_name": tenant.name, "address": outlet.address, "gstin": outlet.gstin or tenant.gstin,
                            "fssai": outlet.fssai, "phone": outlet.phone, "footer": outlet.bill_footer, "logo_url": tenant.logo_url,
                            "brand": tenant.app_name or tenant.name},
                 "token_no": order.token_no, "order_type": order.order_type, "table_label": order.table_label,
                 "car_number": order.car_number})
    from ..services.training import is_training
    req = UpiRequest(tenant_id=auth.tenant_id, outlet_id=order.outlet_id, order_id=order.id, amount_paise=total,
                     provider="test" if is_training(db, order.outlet_id) else cfg.provider,   # training: never real money
                     bill_body={"bill_id": str(body.bill_id), "customer": c.model_dump(mode="json"),
                                "loyalty": body.loyalty.model_dump(mode="json") if body.loyalty else None, "slip": slip},
                     created_by=auth.user_id, expires_at=_now() + timedelta(minutes=cfg.qr_minutes or QR_MINUTES))
    db.add(req)
    db.flush()
    try:
        _create_qr(cfg, req, outlet, order.token_no)
    except (GatewayError, ValueError) as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from None
    db.flush()
    return _out(db, req)


@router.get("/upi/requests/{req_id}")
def upi_request(req_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    """The counter asks every couple of seconds. While waiting, the box checks with Razorpay too, so it
    works even when Razorpay's webhook can't reach the box."""
    req = db.scalar(select(UpiRequest).where(UpiRequest.id == req_id).with_for_update())
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    ensure_outlet_access(db, auth, req.outlet_id)
    if req.status == "waiting":
        cfg = db.get(PaymentSettings, auth.tenant_id)
        if cfg and (req.checked_at is None or _now() - req.checked_at > timedelta(seconds=POLL_SECONDS)):
            req.checked_at = _now()
            paid = _fetch_paid(cfg, req)
            if paid:
                settle(db, req, paid)
        if req.status == "waiting" and _now() > req.expires_at:
            req.status = "closed"
            req.problem = "The QR expired. Make a new one."
    db.flush()
    return _out(db, req)


@router.post("/upi/requests/{req_id}/cancel")
def cancel_upi(req_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    req = db.scalar(select(UpiRequest).where(UpiRequest.id == req_id).with_for_update())
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    ensure_outlet_access(db, auth, req.outlet_id)
    if req.status == "waiting":
        cfg = db.get(PaymentSettings, auth.tenant_id)
        paid = _fetch_paid(cfg, req) if cfg else None
        if paid:                      # paid just before the cashier gave up: bill it, don't lose it
            settle(db, req, paid)
        else:
            if cfg:
                _close_qr(cfg, req)
            req.status = "closed"
    db.flush()
    return _out(db, req)


@router.post("/upi/requests/{req_id}/test-paid")
def test_paid(req_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    """Training only (provider 'test'): pretend the customer paid."""
    req = get_or_404(db, UpiRequest, req_id, "UPI request")
    if req.provider != "test":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only for test mode")
    settle(db, req, rz.Paid(f"test_pay_{uuid.uuid4().hex[:10]}", req.amount_paise, "customer@test"))
    db.flush()
    return _out(db, req)


@router.get("/outlets/{outlet_id}/upi/waiting")
def upi_waiting(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> list[dict]:
    """QRs still out (on a printed slip, or a dialog the cashier closed) and ones paid in the last few minutes.
    The counter asks every few seconds and pops up "Token 12 paid ₹240" when one comes in. While asked,
    the box checks each waiting QR with the provider, so it works without the webhook too."""
    ensure_outlet_access(db, auth, outlet_id)
    since = _now() - timedelta(minutes=10)
    reqs = list(db.scalars(select(UpiRequest).where(UpiRequest.outlet_id == outlet_id, (UpiRequest.status == "waiting") |
                                                    ((UpiRequest.paid_at != None) & (UpiRequest.paid_at > since)))  # noqa: E711
                           .order_by(UpiRequest.created_at).limit(50).with_for_update(skip_locked=True)))
    cfg = db.get(PaymentSettings, auth.tenant_id)
    for req in reqs:
        if req.status != "waiting":
            continue
        if cfg and (req.checked_at is None or _now() - req.checked_at > timedelta(seconds=POLL_SECONDS * 2)):
            req.checked_at = _now()
            paid = _fetch_paid(cfg, req)
            if paid:
                settle(db, req, paid)
        if req.status == "waiting" and _now() > req.expires_at + timedelta(minutes=2):   # a late payment still counts for a bit
            req.status, req.problem = "closed", "The QR expired unpaid"
    db.flush()
    return [_out(db, r) for r in reqs if r.status != "closed"]


# ------------------------------------------------------------------ Paytm server-to-server post (no login)

@router.post("/public/paytm/{key}")
async def paytm_callback(key: str, request: Request) -> dict:
    """Paytm posts here when a QR payment finishes. The post is only a nudge: the box asks Paytm's
    Transaction Status API (signed with the merchant key) before making any bill."""
    with system_session() as sdb:
        cfg = sdb.scalar(select(PaymentSettings).where(PaymentSettings.webhook_key == key))
        tenant_id = cfg.tenant_id if cfg else None
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    data: dict = {}
    ctype = request.headers.get("content-type", "")
    try:
        if "json" in ctype:
            data = await request.json()
        else:
            data = {k: str(v) for k, v in (await request.form()).items()}
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad body") from None
    oid = pt.order_of_post(data if isinstance(data, dict) else {})
    if not oid:
        return {"ok": True, "ignored": True}
    with tenant_session(tenant_id) as db:
        req = db.scalar(select(UpiRequest).where(UpiRequest.provider_ref == oid, UpiRequest.provider == "paytm").with_for_update())
        cfg2 = db.get(PaymentSettings, tenant_id)
        if req is not None and cfg2 is not None and req.status in ("waiting", "closed"):
            req.checked_at = _now()
            paid = _fetch_paid(cfg2, req)
            if paid:
                settle(db, req, paid)
    return {"ok": True}


# ------------------------------------------------------------------ Razorpay webhook (no login: signed)

@router.post("/public/razorpay/{key}")
async def razorpay_webhook(key: str, request: Request, x_razorpay_signature: str | None = Header(default=None)) -> dict:
    raw = await request.body()
    with system_session() as sdb:   # only to find whose webhook this is
        cfg = sdb.scalar(select(PaymentSettings).where(PaymentSettings.webhook_key == key))
        tenant_id, secret = (cfg.tenant_id, cfg.webhook_secret) if cfg else (None, None)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not rz.verify_signature(raw, x_razorpay_signature, secret):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad signature")
    import json
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad body") from None
    got = rz.from_webhook(event)
    if not got:
        return {"ok": True, "ignored": True}
    qr_id, paid = got
    with tenant_session(tenant_id) as db:
        req = db.scalar(select(UpiRequest).where(UpiRequest.provider_ref == qr_id).with_for_update())
        if req is not None:
            settle(db, req, paid)
    return {"ok": True}



# ------------------------------------------------------------------ card machines (Razorpay POS)

class TerminalIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    device_id: str = Field(min_length=3, max_length=60)
    is_active: bool = True


def _term_out(t: CardTerminal) -> dict:
    return {"id": str(t.id), "name": t.name, "device_id": t.device_id, "is_active": t.is_active, "outlet_id": str(t.outlet_id)}


@router.get("/outlets/{outlet_id}/card-terminals")
def list_terminals(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> list[dict]:
    ensure_outlet_access(db, auth, outlet_id)
    return [_term_out(t) for t in db.scalars(select(CardTerminal).where(CardTerminal.outlet_id == outlet_id).order_by(CardTerminal.name))]


@router.post("/outlets/{outlet_id}/card-terminals", status_code=201)
def add_terminal(outlet_id: uuid.UUID, body: TerminalIn, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Outlet, outlet_id, "Outlet")
    if db.scalar(select(CardTerminal.id).where(CardTerminal.outlet_id == outlet_id, CardTerminal.name == body.name.strip())):
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a card machine called {body.name.strip()}")
    t = CardTerminal(tenant_id=auth.tenant_id, outlet_id=outlet_id, name=body.name.strip(), device_id=body.device_id.strip(), is_active=body.is_active)
    db.add(t)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "card_terminal", t.id, after=_term_out(t))
    return _term_out(t)


@router.patch("/card-terminals/{terminal_id}")
def edit_terminal(terminal_id: uuid.UUID, body: TerminalIn, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> dict:
    t = get_or_404(db, CardTerminal, terminal_id, "Card machine")
    t.name, t.device_id, t.is_active = body.name.strip(), body.device_id.strip(), body.is_active
    db.flush()
    return _term_out(t)


def _pos_call(cfg: PaymentSettings, action: str, body: dict) -> dict:
    import httpx
    base = rp.LIVE if cfg.pos_live else rp.DEMO
    try:
        r = httpx.post(f"{base}/{action}", json=body, timeout=10)
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"success": False, "errorMessage": f"Razorpay POS said {r.status_code}"}
    except Exception as e:
        return {"success": False, "errorMessage": f"Couldn't reach Razorpay POS ({e.__class__.__name__})"}


class CardIn(BaseModel):
    order_id: uuid.UUID
    bill_id: uuid.UUID
    terminal_id: uuid.UUID
    customer: CustomerIn = CustomerIn()
    loyalty: LoyaltyUseIn | None = None


def _card_out(db: Session, req: CardRequest) -> dict:
    t = db.get(CardTerminal, req.terminal_id)
    out: dict = {"id": str(req.id), "status": req.status, "amount_paise": req.amount_paise, "provider": req.provider, "terminal": t.name if t else None,
                 "expires_at": req.expires_at.isoformat(), "payment_ref": req.payment_ref, "problem": req.problem, "order_id": str(req.order_id), "bill": None}
    if req.bill_id:
        out["bill"] = bill_out(db, db.get(Bill, req.bill_id)).model_dump(mode="json")
    return out


@router.post("/card/requests")
def new_card_request(body: CardIn, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    """Send this bill's amount to the counter's card machine."""
    cfg = db.get(PaymentSettings, auth.tenant_id)
    if cfg is None or not cfg.pos_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Card machines aren't linked. Type the amount on the machine and tap Card received.")
    order = get_or_404(db, Order, body.order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    term = get_or_404(db, CardTerminal, body.terminal_id, "Card machine")
    if term.outlet_id != order.outlet_id or not term.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That card machine isn't in use at this outlet")
    if db.scalar(select(Bill.id).where(Bill.order_id == order.id, Bill.status == "paid")):
        raise HTTPException(status.HTTP_409_CONFLICT, "This order is already billed")
    c = body.customer
    if not c.phone and not c.declined:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ask for the customer's mobile number, or tap No number")
    busy = db.scalar(select(CardRequest).where(CardRequest.terminal_id == term.id, CardRequest.status == "waiting", CardRequest.expires_at > _now()))
    if busy is not None and busy.order_id != order.id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{term.name} is waiting for another card payment. Finish or cancel that one first.")
    for old in db.scalars(select(CardRequest).where(CardRequest.order_id == order.id, CardRequest.status == "waiting").with_for_update()):
        old.status = "closed"
    rows = billable_rows(db, order)
    if not rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing left to bill on this order")
    from .billing import _loyalty
    from ..services.training import is_training
    disc, _lp, _lr, _why = _loyalty(db, auth.tenant_id, body.loyalty, None, c.phone, rows, order.outlet_id)
    total = compute_bill([r.line for r in rows], disc).total_paise
    req = CardRequest(tenant_id=auth.tenant_id, outlet_id=order.outlet_id, order_id=order.id, terminal_id=term.id, amount_paise=total,
                      provider="test" if is_training(db, order.outlet_id) else cfg.pos_provider,
                      created_by=auth.user_id, expires_at=_now() + timedelta(minutes=5),
                      bill_body={"bill_id": str(body.bill_id), "customer": c.model_dump(mode="json"),
                                 "loyalty": body.loyalty.model_dump(mode="json") if body.loyalty else None})
    db.add(req)
    db.flush()
    if req.provider == "test":
        req.provider_ref = f"test_p2p_{uuid.uuid4().hex[:10]}"
    else:
        try:
            call = rp.pay_body(cfg.pos_username or "", cfg.pos_app_key or "", term.device_id, total, f"{str(order.id)[:8]}-{order.token_no or ''}")
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
        reply = _pos_call(cfg, "pay", call)
        rid = rp.request_id(reply)
        if not rid:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{term.name}: {rp.error_of(reply) or 'no reply'}. Type the amount on the machine and tap Card received.")
        req.provider_ref = rid
    db.flush()
    return _card_out(db, req)


def _settle_card(db: Session, req: CardRequest, result: rp.CardResult) -> None:
    if req.status not in ("waiting", "closed"):
        return
    if result.state == "failed":
        req.status, req.problem = "failed", result.message or "The card payment didn't go through"
        return
    if result.state != "approved":
        return
    req.paid_at, req.payment_ref, req.txn_id = _now(), result.reference() or None, result.txn_id
    if result.amount_paise is not None and result.amount_paise != req.amount_paise:
        req.status, req.problem = "problem", f"The machine took ₹{result.amount_paise / 100:g} but the bill is ₹{req.amount_paise / 100:g}"
        return
    make_bill(db, req, "card", req.payment_ref)


@router.get("/card/requests/{req_id}")
def card_request(req_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    req = db.scalar(select(CardRequest).where(CardRequest.id == req_id).with_for_update())
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    ensure_outlet_access(db, auth, req.outlet_id)
    if req.status == "waiting":
        cfg = db.get(PaymentSettings, auth.tenant_id)
        if cfg and req.provider == "razorpay_pos" and req.provider_ref and (req.checked_at is None or _now() - req.checked_at > timedelta(seconds=2)):
            req.checked_at = _now()
            _settle_card(db, req, rp.parse_status(_pos_call(cfg, "status", rp.status_body(cfg.pos_username or "", cfg.pos_app_key or "", req.provider_ref))))
        if req.status == "waiting" and _now() > req.expires_at:
            req.status, req.problem = "closed", "The machine timed out. Try again."
    db.flush()
    return _card_out(db, req)


@router.post("/card/requests/{req_id}/cancel")
def cancel_card(req_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    req = db.scalar(select(CardRequest).where(CardRequest.id == req_id).with_for_update())
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    ensure_outlet_access(db, auth, req.outlet_id)
    if req.status == "waiting":
        cfg = db.get(PaymentSettings, auth.tenant_id)
        if cfg and req.provider == "razorpay_pos" and req.provider_ref:
            # Approved just before the cashier gave up: bill it rather than lose it.
            _settle_card(db, req, rp.parse_status(_pos_call(cfg, "status", rp.status_body(cfg.pos_username or "", cfg.pos_app_key or "", req.provider_ref))))
            if req.status == "waiting":
                t = db.get(CardTerminal, req.terminal_id)
                _pos_call(cfg, "cancel", rp.cancel_body(cfg.pos_username or "", cfg.pos_app_key or "", req.provider_ref, t.device_id if t else ""))
        if req.status == "waiting":
            req.status = "closed"
    db.flush()
    return _card_out(db, req)


@router.post("/card/requests/{req_id}/test-result")
def card_test_result(req_id: uuid.UUID, approved: bool = True, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    """Training only (test mode): pretend the card was approved or declined."""
    req = get_or_404(db, CardRequest, req_id, "Card request")
    if req.provider != "test":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only for test mode")
    _settle_card(db, req, rp.CardResult("approved", req.amount_paise, "4242", "T" + uuid.uuid4().hex[:5].upper(), "test_txn")
                 if approved else rp.CardResult("failed", message="Card declined (test)"))
    db.flush()
    return _card_out(db, req)
