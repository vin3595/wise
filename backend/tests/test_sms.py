import pytest

from app.core.sms import TEMPLATES, msisdn, next_try, render, rupees, segments


def test_bill_text():
    t = render("bill", {"brand": "Shakewallah", "invoice": "PT1/2627/000012", "amount": rupees(31300),
                        "link": "https://pos.shakewallah.in/b/Ab3dE9"})
    assert t == ("Thank you for visiting Shakewallah. Your bill PT1/2627/000012 is Rs 313. "
                 "View it here: https://pos.shakewallah.in/b/Ab3dE9")
    assert segments(t) == 1


def test_missing_value_refused():
    with pytest.raises(ValueError):
        render("ready", {"brand": "Shakewallah", "token": None})


def test_every_template_fits_two_parts():
    long = {v: "X" * 12 for t in TEMPLATES.values() for v in t["vars"]}
    for kind in TEMPLATES:
        assert segments(render(kind, long)) <= 2, kind


def test_indian_rupees():
    assert rupees(31300) == "313" and rupees(5) == "0.05" and rupees(123456789) == "12,34,567.89"
    assert rupees(10000000) == "1,00,000"


def test_phone():
    assert msisdn("98290 12345") == "919829012345" and msisdn("+91 9829012345") == "919829012345"
    with pytest.raises(ValueError):
        msisdn("12345")


def test_backoff_gives_up():
    assert next_try(1).total_seconds() == 60 and next_try(6) is not None and next_try(7) is None
