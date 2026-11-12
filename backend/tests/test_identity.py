from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.contact import mask_identifier, normalize_identifier, normalize_phone, validate_gstin, validate_hex_color
from app.core.login_id import login_id_from_host, validate_login_id
from app.core.permissions import Perm, Role, can_assign, has_perm
from app.core.security import (
    create_token, decode_token, generate_otp, hash_otp, hash_secret, otp_matches, pin_lookup_key, validate_pin,
    verify_secret,
)

SECRET = "test-secret-that-is-at-least-32-bytes-long"


@pytest.mark.parametrize("raw,expected", [("Shakewallah", "shakewallah"), (" sw-jodhpur ", "sw-jodhpur"),
                                          ("pizza2go", "pizza2go")])
def test_login_id_ok(raw, expected):
    assert validate_login_id(raw) == expected


@pytest.mark.parametrize("raw", ["ab", "1shake", "shake_wallah", "shake--w", "shake-", "admin", "a" * 31, "शेक"])
def test_login_id_bad(raw):
    with pytest.raises(ValueError):
        validate_login_id(raw)


def test_login_id_from_host():
    assert login_id_from_host("shakewallah.wise.in", "wise.in") == "shakewallah"
    assert login_id_from_host("shakewallah.wise.in:443", "wise.in") == "shakewallah"
    assert login_id_from_host("wise.in", "wise.in") is None
    assert login_id_from_host("www.wise.in", "wise.in") is None
    assert login_id_from_host("a.b.wise.in", "wise.in") is None
    assert login_id_from_host("pos.shakewallah.com", "wise.in") is None


@pytest.mark.parametrize("raw", ["9829012345", "+91 98290 12345", "919829012345", "09829012345", "98290-12345"])
def test_phone_ok(raw):
    assert normalize_phone(raw) == "9829012345"


@pytest.mark.parametrize("raw", ["12345", "5829012345", "98290123456", ""])
def test_phone_bad(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_identifier_and_mask():
    assert normalize_identifier("Owner@Shake.IN") == ("email", "owner@shake.in")
    assert normalize_identifier("9829012345") == ("phone", "9829012345")
    assert mask_identifier("phone", "9829012345") == "******2345"
    assert mask_identifier("email", "owner@shake.in") == "ow***@shake.in"


def test_gstin_and_color():
    assert validate_gstin("08abcde1234f1z5") == "08ABCDE1234F1Z5"
    assert validate_gstin("") is None
    with pytest.raises(ValueError):
        validate_gstin("08ABCDE1234")
    assert validate_hex_color("#FF8800") == "#ff8800"
    with pytest.raises(ValueError):
        validate_hex_color("orange")


def test_pin_hash_roundtrip():
    h = hash_secret("4821")
    assert verify_secret("4821", h)
    assert not verify_secret("4822", h)
    assert not verify_secret("4821", None)
    assert not verify_secret("4821", "garbage")
    assert hash_secret("4821") != h  # salted


@pytest.mark.parametrize("pin", ["1111", "1234", "12", "abcd", "12345"])
def test_weak_pins_rejected(pin):
    with pytest.raises(ValueError):
        validate_pin(pin)


def test_pin_lookup_is_tenant_scoped():
    a = pin_lookup_key("4821", "tenant-a", SECRET)
    assert a == pin_lookup_key("4821", "tenant-a", SECRET)
    assert a != pin_lookup_key("4821", "tenant-b", SECRET)
    assert a != pin_lookup_key("4821", "tenant-a", "other-secret-32-bytes-long-xxxxxxxxx")


def test_otp():
    code = generate_otp()
    assert len(code) == 6 and code.isdigit()
    h = hash_otp(code, "9829012345", SECRET)
    assert otp_matches(code, "9829012345", SECRET, h)
    assert not otp_matches(code, "9829012346", SECRET, h)


def test_tokens():
    tok = create_token({"sub": "u1", "tid": "t1", "role": "owner"}, SECRET, hours=1)
    claims = decode_token(tok, SECRET)
    assert claims["sub"] == "u1" and claims["tid"] == "t1"
    old = create_token({"sub": "u1", "tid": "t1"}, SECRET, hours=1,
                       now=datetime.now(timezone.utc) - timedelta(hours=2))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(old, SECRET)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(tok, "wrong-secret-that-is-also-32-bytes-long")


def test_permissions():
    assert has_perm(Role.owner, Perm.tenant_settings)
    assert has_perm(Role.manager, Perm.menu_override)
    assert not has_perm(Role.manager, Perm.menu_manage)
    assert not has_perm(Role.cashier, Perm.costing_view)
    assert not has_perm(Role.captain, Perm.billing)
    assert has_perm(Role.kitchen, Perm.kds_view)
    assert can_assign(Role.manager, Role.cashier)
    assert not can_assign(Role.manager, Role.manager)
    assert not can_assign(Role.cashier, Role.kitchen)
