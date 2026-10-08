"""Every message has its own Date and Message-ID, so nothing downstream adds them: Cloudflare signs "date" even
when it's missing, and a receiving server that adds one (Microsoft does) breaks the DKIM signature."""
from __future__ import annotations

from email.utils import parsedate_to_datetime

from portal.mail import Mail, _mime


def test_date_and_message_id_are_set():
    msg = _mime(Mail(to="person@example.org", subject="Your sign-in code", text="123456"),
                "PDF Remediation", "no-reply@example.edu")
    assert parsedate_to_datetime(msg["Date"]).utcoffset().total_seconds() == 0
    assert msg["Message-ID"].startswith("<") and msg["Message-ID"].endswith("@example.edu>")
    other = _mime(Mail(to="person@example.org", subject="x", text="y"), "", "no-reply@example.edu")
    assert other["Message-ID"] != msg["Message-ID"]


def test_the_sign_in_code_is_only_in_the_body():
    from portal.mail import sign_in_code_mail
    m = sign_in_code_mail("person@example.org", "482913", 10, "Campus Accessibility")
    assert "482913" not in m.subject and m.subject == "Your sign-in code for Campus Accessibility"
    assert "482913" in m.text
