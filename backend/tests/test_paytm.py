import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.core import paytm as pt

KEY = "kbzk1DSbJiV_O3p5"   # 16 chars, the shape of a Paytm merchant key


def test_signature_round_trip_and_shape():
    body = json.dumps({"mid": "M1", "orderId": "W1"}, separators=(",", ":"))
    sig = pt.sign(body, KEY)
    assert len(sig) == 108                       # Paytm's documented signature length
    assert pt.verify(body, KEY, sig)
    assert not pt.verify(body + " ", KEY, sig)
    assert not pt.verify(body, "x" * 16, sig)
    assert not pt.verify(body, KEY, "not-base64")
    assert not pt.verify(body, KEY, None)


def test_signature_is_sha256_hex_plus_salt_under_aes():
    sig = pt.sign("abc", KEY, salt="Zx9q")
    plain = pt._decrypt(sig, KEY)
    assert plain == hashlib.sha256(b"abc|Zx9q").hexdigest() + "Zx9q"
    assert pt.sign("abc", KEY, salt="Zx9q") == sig  # same salt, same signature (CBC with fixed IV)


def test_form_params_sorted_skip_checksum_and_null():
    assert pt.form_params({"ORDERID": "W1", "MID": "M", "CHECKSUMHASH": "x", "BANKNAME": "null", "A": "1"}) == "1||M|W1"


def test_signed_request_body_matches_signature():
    raw = pt.signed({"mid": "M", "orderId": "W1"}, KEY, "C11")
    got = json.loads(raw)
    b = json.dumps(got["body"], separators=(",", ":"))
    assert got["head"]["clientId"] == "C11" and got["head"]["version"] == "v1"
    assert pt.verify(b, KEY, got["head"]["signature"])
    assert "clientId" not in json.loads(pt.signed({"mid": "M"}, KEY, None))["head"]


def test_bad_key_length():
    with pytest.raises(ValueError):
        pt.sign("x", "short")


def test_qr_body():
    exp = datetime(2026, 11, 11, 6, 0, 0, tzinfo=timezone.utc)
    b = pt.qr_body("MID1", "W123", 24050, "SHK_1", exp, "Shakewallah Paota", "Token 12")
    assert b == {"mid": "MID1", "orderId": "W123", "amount": "240.50", "businessType": "UPI_QR_CODE", "posId": "SHK_1",
                 "expiryDate": "2026-11-11 11:30:00", "displayName": "Shakewallah Paota", "orderDetails": "Token 12"}
    assert pt.qr_body("M", "W", 5, "P_1", None)["amount"] == "0.05"
    with pytest.raises(ValueError):
        pt.qr_body("M", "W", 0, "P_1", None)
    with pytest.raises(ValueError):
        pt.qr_body("", "W", 100, "P_1", None)


def test_ids():
    assert pt.order_id("0f" * 16) == "W" + ("0f" * 16)[:31]
    assert len(pt.order_id("a" * 32)) == 32
    assert pt.pos_id("shk-1") == "SHK1_1"
    assert pt.pos_id("--") == "OUT_1"


def test_qr_from_reply():
    ok = {"body": {"resultInfo": {"resultStatus": "SUCCESS", "resultCode": "QR_0001"}, "qrCodeId": "Q1", "qrData": "upi://pay?pa=x@paytm&am=1"}}
    assert pt.qr_from_reply(ok) == ("upi://pay?pa=x@paytm&am=1", "Q1")
    with pytest.raises(pt.PaytmError, match="Invalid MID"):
        pt.qr_from_reply({"body": {"resultInfo": {"resultStatus": "FAILURE", "resultMsg": "Invalid MID", "resultCode": "QR_1001"}}})
    with pytest.raises(pt.PaytmError):
        pt.qr_from_reply({})


def test_read_status():
    paid = pt.read_status({"body": {"resultInfo": {"resultStatus": "TXN_SUCCESS"}, "txnId": "T1", "bankTxnId": "631512345678",
                                    "txnAmount": "240.50", "paymentMode": "UPI"}})
    assert paid.state == "paid" and paid.paid and paid.paid.payment_id == "631512345678" and paid.paid.amount_paise == 24050
    assert pt.read_status({"body": {"resultInfo": {"resultStatus": "TXN_SUCCESS"}, "txnId": "T9", "txnAmount": "5"}}).paid.amount_paise == 500
    assert pt.read_status({"body": {"resultInfo": {"resultStatus": "PENDING"}}}).state == "pending"
    assert pt.read_status({"body": {"resultInfo": {"resultStatus": "NO_RECORD_FOUND"}}}).state == "pending"
    f = pt.read_status({"body": {"resultInfo": {"resultStatus": "TXN_FAILURE", "resultMsg": "Insufficient balance"}}})
    assert f.state == "failed" and f.message == "Insufficient balance" and f.paid is None
    assert pt.read_status({}).state == "unknown"
    assert pt.read_status({"body": {"resultInfo": {"resultStatus": "TXN_SUCCESS"}}}).state == "unknown"  # no reference


def test_order_of_post():
    assert pt.order_of_post({"ORDERID": "W1", "STATUS": "TXN_SUCCESS"}) == "W1"
    assert pt.order_of_post({"body": {"orderId": "W2"}}) == "W2"
    assert pt.order_of_post({"x": 1}) is None
