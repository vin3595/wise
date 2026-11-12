"""Razorpay UPI QR: request building, webhook signature check, reading payments (pure, unit-tested).

Flow: the counter asks for a QR for the exact bill amount (single use, fixed amount, closes in 15 minutes).
The customer pays with any UPI app; Razorpay calls our webhook (qr_code.credited), or the box asks Razorpay
for the QR's payments while the counter waits. Either way the bill is made once, with the payment id.
API: https://razorpay.com/docs/api/qr-codes/
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

API = "https://api.razorpay.com/v1"


def verify_signature(raw_body: bytes, signature: str | None, secret: str | None) -> bool:
    """X-Razorpay-Signature = hex HMAC-SHA256 of the raw body with the webhook secret."""
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def qr_request(amount_paise: int, name: str, description: str, close_by: int, notes: dict[str, str]) -> dict:
    if amount_paise <= 0:
        raise ValueError("Amount must be more than zero")
    return {"type": "upi_qr", "name": name[:40], "usage": "single_use", "fixed_amount": True,
            "payment_amount": amount_paise, "description": description[:100], "close_by": close_by,
            "notes": {k: str(v)[:250] for k, v in list(notes.items())[:15]}}


@dataclass(frozen=True)
class Paid:
    payment_id: str
    amount_paise: int
    payer: str | None


def from_payment(p: dict) -> Paid | None:
    """A payment entity that has really come in (captured; 'authorized' UPI is captured automatically)."""
    if p.get("status") not in ("captured", "authorized"):
        return None
    payer = p.get("vpa") or (p.get("upi") or {}).get("vpa") or (p.get("acquirer_data") or {}).get("rrn")
    return Paid(str(p["id"]), int(p.get("amount", 0)), payer)


def from_webhook(body: dict) -> tuple[str, Paid] | None:
    """(QR id, payment) from a qr_code.credited event; None for anything else."""
    if body.get("event") != "qr_code.credited":
        return None
    pl = body.get("payload") or {}
    qr = ((pl.get("qr_code") or {}).get("entity") or {}).get("id")
    pay = from_payment(((pl.get("payment") or {}).get("entity")) or {})
    return (str(qr), pay) if qr and pay else None


def first_paid(items: list[dict]) -> Paid | None:
    for p in items:
        got = from_payment(p)
        if got:
            return got
    return None


def mismatch(expected_paise: int, paid: Paid) -> str | None:
    if paid.amount_paise != expected_paise:
        return f"Customer paid ₹{paid.amount_paise / 100:g} but the bill is ₹{expected_paise / 100:g}"
    return None
