import hashlib
import hmac
import json

import pytest

from app.core.razorpay import first_paid, from_webhook, mismatch, qr_request, verify_signature


def test_signature():
    body = json.dumps({"event": "qr_code.credited"}).encode()
    sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, "whsec")
    assert not verify_signature(body, sig, "other")
    assert not verify_signature(body + b" ", sig, "whsec")
    assert not verify_signature(body, None, "whsec") and not verify_signature(body, sig, None)


def test_qr_request():
    r = qr_request(31300, "Shakewallah Paota counter", "Token 12", 1_800_000_000, {"order": "abc"})
    assert r["usage"] == "single_use" and r["fixed_amount"] and r["payment_amount"] == 31300 and r["type"] == "upi_qr"
    with pytest.raises(ValueError):
        qr_request(0, "x", "y", 1, {})


def test_webhook_and_payments():
    ev = {"event": "qr_code.credited", "payload": {
        "payment": {"entity": {"id": "pay_29QQoUBi66xm2f", "amount": 31300, "status": "captured", "vpa": "ravi@okaxis"}},
        "qr_code": {"entity": {"id": "qr_HMsVL8HOpbMcjU", "payments_amount_received": 31300}}}}
    qr, paid = from_webhook(ev)
    assert qr == "qr_HMsVL8HOpbMcjU" and paid.payment_id == "pay_29QQoUBi66xm2f" and paid.payer == "ravi@okaxis"
    assert from_webhook({"event": "qr_code.closed", "payload": {}}) is None
    failed = {**ev, "payload": {**ev["payload"], "payment": {"entity": {"id": "p", "amount": 1, "status": "failed"}}}}
    assert from_webhook(failed) is None
    assert first_paid([{"id": "a", "status": "failed", "amount": 5}, {"id": "b", "status": "captured", "amount": 5}]).payment_id == "b"
    assert first_paid([]) is None
    assert mismatch(31300, paid) is None and "paid ₹300" in mismatch(31300, paid.__class__("x", 30000, None))
