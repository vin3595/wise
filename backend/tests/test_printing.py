from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.core.printing import job_kind
from app.schemas import PrepAreaIn

T = datetime(2026, 11, 10, tzinfo=timezone.utc)


def test_first_print():
    assert job_kind("new", None, None, False) == "kot"
    assert job_kind("preparing", None, None, False) == "kot"  # chef started from the screen before it printed


def test_printed_once_only():
    assert job_kind("new", T, None, False) is None


def test_reprint():
    assert job_kind("preparing", T, None, True) == "reprint"


def test_cancel_slip_only_if_kitchen_has_paper():
    assert job_kind("cancelled", None, None, False) is None
    assert job_kind("cancelled", T, None, False) == "cancel"
    assert job_kind("cancelled", T, T, False) is None
    assert job_kind("cancelled", T, None, True) == "cancel"


def test_prep_area_printer_fields():
    a = PrepAreaIn(name="Kitchen", printer_host=" 192.168.1.50 ", paper_mm=58)
    assert a.printer_host == "192.168.1.50" and a.printer_port == 9100 and a.kot_copies == 1
    assert PrepAreaIn(name="Shakes", printer_host="").printer_host is None
    with pytest.raises(ValidationError):
        PrepAreaIn(name="X", paper_mm=72)
    with pytest.raises(ValidationError):
        PrepAreaIn(name="X", printer_host="192.168.1.50; rm -rf")
    with pytest.raises(ValidationError):
        PrepAreaIn(name="X", kot_copies=5)
