"""Paytm Dynamic QR: signature, QR request, status reading (pure, unit-tested).

Flow (same as Petpooja's): the counter asks Paytm for a QR for the exact bill amount; the QR is shown on
screen or printed on the bill; the customer pays with any UPI app; Paytm posts the result to our URL
(server-to-server) and the box also asks Paytm's Transaction Status API while the payment is waiting.
The posted result is only a nudge: the box always re-asks Paytm before billing, so a forged post can't
mark a bill paid.

Docs: https://www.paytmpayments.com/docs/dynamic-qr-code-payments
      https://paytmpayments.com/docs/api/create-qr-code-api
      https://www.paytmpayments.com/docs/api/v3/transaction-status-api
Signature (Paytm "checksum"): SHA-256 of "<params>|<4-char salt>", hex + salt, AES-128-CBC with the
merchant key and Paytm's fixed IV, base64. JSON APIs sign the exact body string.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .razorpay import Paid

STAGE = "https://securegw-stage.paytmpayments.in"
LIVE = "https://securegw.paytmpayments.in"   # Paytm's production host; confirm with Paytm when going live
QR_CREATE = "/paymentservices/qr/create"
STATUS = "/v3/order/status"
IV = b"@@@@&&&&####$$$$"
IST = timezone(timedelta(hours=5, minutes=30))
_SALT_CHARS = string.ascii_letters + string.digits


# ------------------------------------------------------------------ checksum

def _aes(key: str):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    k = key.encode()
    if len(k) not in (16, 24, 32):
        raise ValueError("The Paytm merchant key looks wrong (it should be 16 characters)")
    return Cipher(algorithms.AES(k), modes.CBC(IV))


def _encrypt(text: str, key: str) -> str:
    data = text.encode()
    pad = 16 - len(data) % 16
    enc = _aes(key).encryptor()
    return base64.b64encode(enc.update(data + bytes([pad]) * pad) + enc.finalize()).decode()


def _decrypt(b64: str, key: str) -> str:
    dec = _aes(key).decryptor()
    raw = dec.update(base64.b64decode(b64)) + dec.finalize()
    pad = raw[-1]
    if not 1 <= pad <= 16:
        raise ValueError("bad padding")
    return raw[:-pad].decode()


def _hash(params: str, salt: str) -> str:
    return hashlib.sha256(f"{params}|{salt}".encode()).hexdigest() + salt


def sign(params: str, key: str, salt: str | None = None) -> str:
    salt = salt or "".join(secrets.choice(_SALT_CHARS) for _ in range(4))
    return _encrypt(_hash(params, salt), key)


def verify(params: str, key: str, signature: str | None) -> bool:
    if not signature or not key:
        return False
    try:
        got = _decrypt(signature, key)
    except Exception:
        return False
    return secrets.compare_digest(got, _hash(params, got[-4:]))


def form_params(form: dict[str, str]) -> str:
    """Key-value posts are signed as their values sorted by key, joined with '|' (CHECKSUMHASH left out)."""
    vals = []
    for k in sorted(form):
        if k == "CHECKSUMHASH":
            continue
        v = form[k]
        vals.append("" if v is None or str(v).lower() == "null" else str(v))
    return "|".join(vals)


def signed(body: dict, key: str, client_id: str | None) -> str:
    """The whole JSON request, with the signature over the exact body string that is sent."""
    b = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    head = {"version": "v1", "signature": sign(b, key)}
    if client_id:
        head["clientId"] = client_id
    # Built by hand so the body bytes are exactly the ones that were signed.
    return '{"body":' + b + ',"head":' + json.dumps(head, separators=(",", ":")) + "}"


# ------------------------------------------------------------------ requests

def order_id(request_id_hex: str) -> str:
    """Unique per merchant across all counters: W + the Wise request id."""
    return "W" + request_id_hex.replace("-", "")[:31]


def pos_id(outlet_code: str, counter: str = "1") -> str:
    """Paytm wants 'StoreID_POSID' (alphanumeric)."""
    clean = "".join(ch for ch in outlet_code.upper() if ch.isalnum()) or "OUT"
    return f"{clean}_{counter}"[:50]


def qr_body(mid: str, oid: str, amount_paise: int, pos: str, expires_at: datetime | None,
            display_name: str = "", details: str = "") -> dict:
    if amount_paise <= 0:
        raise ValueError("Amount must be more than zero")
    if not mid:
        raise ValueError("Enter the Paytm Merchant ID (MID)")
    body: dict = {"mid": mid, "orderId": oid, "amount": f"{amount_paise // 100}.{amount_paise % 100:02d}",
                  "businessType": "UPI_QR_CODE", "posId": pos}
    if expires_at is not None:
        body["expiryDate"] = expires_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    if display_name:
        body["displayName"] = display_name[:50]
    if details:
        body["orderDetails"] = details[:100]
    return body


def status_body(mid: str, oid: str) -> dict:
    return {"mid": mid, "orderId": oid}


class PaytmError(Exception):
    pass


def qr_from_reply(reply: dict) -> tuple[str, str | None]:
    """(UPI data to draw as a QR, Paytm's QR id). Raises PaytmError with Paytm's own message."""
    body = reply.get("body") or {}
    info = body.get("resultInfo") or {}
    if info.get("resultStatus") != "SUCCESS" or not body.get("qrData"):
        msg = info.get("resultMsg") or "Paytm didn't make the QR"
        code = info.get("resultCode")
        raise PaytmError(f"Paytm: {msg}" + (f" ({code})" if code else ""))
    return str(body["qrData"]), (str(body["qrCodeId"]) if body.get("qrCodeId") else None)


def _paise(amount: str | float | int | None) -> int:
    if amount is None or amount == "":
        return 0
    s = str(amount).strip()
    rupees, _, frac = s.partition(".")
    return int(rupees or 0) * 100 + int((frac + "00")[:2])


@dataclass(frozen=True)
class Status:
    state: str            # paid | pending | failed | unknown
    paid: Paid | None = None
    message: str | None = None


def read_status(reply: dict) -> Status:
    """Transaction Status API reply → paid / pending / failed."""
    body = reply.get("body") or {}
    info = body.get("resultInfo") or {}
    st = info.get("resultStatus")
    if st == "TXN_SUCCESS":
        ref = body.get("bankTxnId") or body.get("txnId")
        payer = (body.get("customerDetails") or {}).get("vpa") if isinstance(body.get("customerDetails"), dict) else None
        if not ref:
            return Status("unknown", message="Paytm said paid but gave no reference")
        return Status("paid", Paid(str(ref), _paise(body.get("txnAmount")), payer or body.get("paymentMode")))
    if st == "TXN_FAILURE":
        return Status("failed", message=info.get("resultMsg") or "The payment failed")
    if st in ("PENDING", "NO_RECORD_FOUND"):
        return Status("pending")
    return Status("unknown", message=info.get("resultMsg"))


def order_of_post(data: dict) -> str | None:
    """Which order Paytm's server-to-server post is about (form or JSON)."""
    for k in ("ORDERID", "orderId", "ORDER_ID"):
        if data.get(k):
            return str(data[k])
    body = data.get("body")
    if isinstance(body, dict) and body.get("orderId"):
        return str(body["orderId"])
    return None
