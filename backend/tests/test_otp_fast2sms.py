import pytest

pytest.importorskip("pydantic_settings")

from app.config import get_settings
from app.services import otp


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def _setup(monkeypatch, resp):
    s = get_settings()
    monkeypatch.setattr(s, "otp_provider", "fast2sms")
    monkeypatch.setattr(s, "fast2sms_api_key", "KEY123")
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(url=url, params=params)
        return resp

    monkeypatch.setattr(otp.httpx, "get", fake_get)
    return seen


def test_fast2sms_sends_code_on_otp_route(monkeypatch):
    seen = _setup(monkeypatch, _Resp(200, {"return": True, "request_id": "x"}))
    otp._deliver("9876543210", "123456")
    assert seen["url"].endswith("/dev/bulkV2")
    assert seen["params"]["route"] == "otp" and seen["params"]["variables_values"] == "123456"
    assert seen["params"]["numbers"] == "9876543210" and seen["params"]["authorization"] == "KEY123"


def test_fast2sms_failure_is_a_clear_error(monkeypatch):
    _setup(monkeypatch, _Resp(200, {"return": False, "message": "Invalid Authentication"}))
    with pytest.raises(otp.OtpError, match="Couldn't send"):
        otp._deliver("9876543210", "123456")


def test_fast2sms_strips_the_restaurant_prefix(monkeypatch):
    seen = _setup(monkeypatch, _Resp(200, {"return": True}))
    otp._deliver("f7efe195-770c-48bb-9c79-c44978eae2e6:7725986613", "392229")
    assert seen["params"]["numbers"] == "7725986613"
