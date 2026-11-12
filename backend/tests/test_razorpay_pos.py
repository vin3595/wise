import pytest

from app.core.razorpay_pos import cancel_body, error_of, parse_status, pay_body, request_id, rupees, status_body


def test_bodies():
    b = pay_body("9876543210", "key", "1490012345|ezetap_android", 31350, "PT1-token-12")
    assert b == {"username": "9876543210", "appKey": "key", "amount": "313.50", "externalRefNumber": "PT1-token-12",
                 "pushTo": {"deviceId": "1490012345|ezetap_android"}, "mode": "CARD"}
    assert status_body("u", "k", "r1") == {"username": "u", "appKey": "k", "origP2pRequestId": "r1"}
    assert cancel_body("u", "k", "r1", "d")["pushTo"] == {"deviceId": "d"}
    assert rupees(5) == "0.05"
    with pytest.raises(ValueError):
        pay_body("u", "k", "d", 0, "x")


def test_replies():
    assert request_id({"success": True, "p2pRequestId": "P2P123"}) == "P2P123"
    assert request_id({"success": False, "errorMessage": "Device not linked"}) is None
    assert error_of({"success": False, "errorMessage": "Device not linked"}) == "Device not linked"
    assert error_of({"success": True, "p2pRequestId": "x"}) is None


def test_status():
    w = parse_status({"success": True, "messageCode": "P2P_DEVICE_RECEIVED", "status": ""})
    assert w.state == "waiting"
    ok = parse_status({"success": True, "status": "AUTHORIZED", "amount": 313.5, "authCode": "A1B2C3", "txnId": "T9",
                       "formattedPan": "4242-XXXX-XXXX-1234"})
    assert ok.state == "approved" and ok.amount_paise == 31350 and ok.last4 == "1234" and ok.reference() == "card 1234 · auth A1B2C3"
    nested = parse_status({"success": True, "txn": {"status": "AUTHORIZED", "amount": "10.00", "cardLastFourDigit": "9876", "txnId": "T1"}})
    assert nested.state == "approved" and nested.last4 == "9876" and nested.amount_paise == 1000
    no = parse_status({"success": True, "messageCode": "P2P_DEVICE_CANCELED", "message": "Cancelled on device"})
    assert no.state == "failed" and no.message == "Cancelled on device"
    assert parse_status({"status": "FAILED"}).state == "failed"
