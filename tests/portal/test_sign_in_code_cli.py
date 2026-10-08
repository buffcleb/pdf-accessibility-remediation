"""Break-glass sign-in: an operator issues a code from the command line when mail isn't arriving. Same checks as the
sign-in page; the code works once and replaces earlier ones; the audit log says who and why, never the code."""
from __future__ import annotations

import re

from sqlalchemy import select

from portal import cli
from portal.models import AuditEvent
from tests.portal.conftest import last_code, make_group, make_user


def _issue(capsys, email, *extra) -> tuple[int, str]:
    rc = cli.main(["sign-in-code", email, "--operator", "ops@laptop", *extra])
    out = capsys.readouterr()
    return rc, out.out + out.err


def test_a_printed_code_signs_in_once(db, make_client, capsys):
    g = make_group(db, "Campus A", "a.test")
    make_user(db, "person@a.test", g)
    db.commit()
    c = make_client()
    # A code mailed earlier stops working once a new one is issued
    assert c.post("/api/v1/auth/code", json={"email": "person@a.test"}).status_code == 202
    mailed = last_code("person@a.test")
    rc, out = _issue(capsys, "Person@A.test", "--reason", "mail outage")
    assert rc == 0
    code = re.search(r"\b(\d{6})\b", out).group(1)
    assert c.post("/api/v1/auth/code/verify", json={"email": "person@a.test", "code": mailed}).status_code != 200
    assert c.post("/api/v1/auth/code/verify", json={"email": "person@a.test", "code": code}).status_code == 200
    assert c.post("/api/v1/auth/code/verify", json={"email": "person@a.test", "code": code}).status_code != 200
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "login.code_issued")).one()
    assert ev.actor_email == "person@a.test" and ev.details == {"by": "operator", "operator": "ops@laptop",
                                                                "reason": "mail outage"}
    assert code not in str(ev.details)


def test_the_same_checks_as_the_sign_in_page(db, capsys):
    g = make_group(db, "Campus A", "a.test")
    u = make_user(db, "gone@a.test", g)
    u.status = "disabled"
    db.commit()
    rc, out = _issue(capsys, "stranger@nowhere.test")
    assert rc == 2 and "can't sign in" in out and not re.search(r"\b\d{6}\b", out)
    rc, out = _issue(capsys, "gone@a.test")
    assert rc == 2 and "disabled" in out
    # A new address in a group's domain may sign in (its account is made on first sign-in)
    rc, out = _issue(capsys, "new@a.test")
    assert rc == 0 and re.search(r"\b\d{6}\b", out)
