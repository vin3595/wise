"""Razorpay POS (Ezetap) card machines, "push to POS" (P2P adapter) API (pure, unit-tested).

Billing sends the amount to a machine by its device id; the machine lights up; the box polls the request's
status until it is approved, failed or cancelled. Endpoints and fields follow Razorpay POS's P2P adapter
(pay / status / cancel with username, appKey, origP2pRequestId, pushTo.deviceId). Razorpay gives each
restaurant its own username, app key and device ids. The status reply's field names are read defensively:
check them against the live sandbox once Razorpay POS shares the credentials.
"""
from __future__ import annotations

from dataclasses import dataclass

DEMO = "https://demo.ezetap.com/api/3.0/p2padapter"
LIVE = "https://www.ezetap.com/api/3.0/p2padapter"

APPROVED = {"AUTHORIZED", "SUCCESS", "SETTLED", "COMPLETED", "P2P_DEVICE_TXN_DONE"}
FAILED = {"FAILED", "DECLINED", "VOIDED", "REVERSED", "P2P_DEVICE_CANCELED", "P2P_STATUS_IN_EXPIRED", "P2P_STATUS_EXPIRED",
          "P2P_DEVICE_CANCELLED", "CANCELLED", "CANCELED"}


def rupees(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


def pay_body(username: str, app_key: str, device_id: str, amount_paise: int, ref: str, mode: str = "CARD") -> dict:
    if amount_paise <= 0:
        raise ValueError("Amount must be more than zero")
    return {"username": username, "appKey": app_key, "amount": rupees(amount_paise), "externalRefNumber": ref[:50],
            "pushTo": {"deviceId": device_id}, "mode": mode}


def status_body(username: str, app_key: str, request_id: str) -> dict:
    return {"username": username, "appKey": app_key, "origP2pRequestId": request_id}


def cancel_body(username: str, app_key: str, request_id: str, device_id: str) -> dict:
    return {"username": username, "appKey": app_key, "origP2pRequestId": request_id, "pushTo": {"deviceId": device_id}}


def request_id(reply: dict) -> str | None:
    """The id to poll with, from the pay reply."""
    if reply.get("success") is False:
        return None
    rid = reply.get("p2pRequestId") or reply.get("origP2pRequestId") or reply.get("requestId")
    return str(rid) if rid else None


def error_of(reply: dict) -> str | None:
    if reply.get("success") is False or reply.get("errorCode") or reply.get("errorMessage"):
        return str(reply.get("errorMessage") or reply.get("message") or reply.get("errorCode") or "The card machine refused the request")
    return None


@dataclass(frozen=True)
class CardResult:
    state: str                    # waiting | approved | failed
    amount_paise: int | None = None
    last4: str | None = None
    auth_code: str | None = None
    txn_id: str | None = None
    rrn: str | None = None
    message: str | None = None

    def reference(self) -> str:
        parts = []
        if self.last4:
            parts.append(f"card {self.last4}")
        if self.auth_code:
            parts.append(f"auth {self.auth_code}")
        if not parts and self.txn_id:
            parts.append(f"txn {self.txn_id}")
        return " · ".join(parts)[:60]


def _paise(v) -> int | None:
    try:
        return round(float(v) * 100)
    except (TypeError, ValueError):
        return None


def parse_status(reply: dict) -> CardResult:
    """What the status reply says. The transaction details may be at the top level or under 'txn'."""
    raw = reply.get("txn")
    txn: dict = raw if isinstance(raw, dict) else {}
    flat = {**reply, **txn}
    code = str(flat.get("status") or flat.get("abstractPaymentStatus") or "").upper()
    msg_code = str(reply.get("messageCode") or "").upper()
    last4 = flat.get("cardLastFourDigit") or flat.get("last4Digits") or flat.get("cardLast4")
    if not last4 and flat.get("formattedPan"):
        last4 = str(flat["formattedPan"])[-4:]
    def make(state: str, message: str | None) -> CardResult:
        return CardResult(state, _paise(flat.get("amount")), str(last4)[-4:] if last4 else None,
                          str(flat["authCode"]) if flat.get("authCode") else None, str(flat["txnId"]) if flat.get("txnId") else None,
                          str(flat.get("rrNumber") or flat.get("rrn")) if (flat.get("rrNumber") or flat.get("rrn")) else None, message)
    if code in APPROVED or msg_code in APPROVED:
        return make("approved", None)
    if code in FAILED or msg_code in FAILED:
        return make("failed", str(reply.get("message") or flat.get("statusDescription") or code or msg_code))
    return make("waiting", str(reply.get("message") or "") or None)
