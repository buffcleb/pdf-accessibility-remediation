"""#456: the audit log to the campus SIEM (portal/siem.py). Events go in order, in batches, from a saved position; a
failure is retried with backoff and nothing is skipped; the syslog messages are RFC 5424, framed by octet counting,
over TLS (against a real TLS listener here); the HTTPS formats; the pull API and its token; the alert when it falls an
hour behind."""
from __future__ import annotations

import datetime as dt
import json
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import select

from portal import alerts, siem
from portal.config import get_portal_settings
from portal.models import AuditEvent
from tests.portal.conftest import login, make_group, make_user

NOW = datetime.now(timezone.utc)


class Recorder:
    def __init__(self, fail: int = 0):
        self.batches: list[list[dict]] = []
        self.fail = fail

    def send(self, batch):
        if self.fail:
            self.fail -= 1
            raise ConnectionRefusedError("collector away")
        self.batches.append(batch)


@pytest.fixture
def siem_on(monkeypatch):
    s = get_portal_settings()
    monkeypatch.setattr(s, "siem_mode", "syslog")
    monkeypatch.setattr(s, "siem_batch", 5)
    return s


def _events(db, n, *, age=timedelta(minutes=10), action="login", outcome="success", group=None):
    for k in range(n):
        db.add(AuditEvent(action=action, outcome=outcome, actor_email=f"p{k}@a.test", ip="198.51.100.4",
                          created_at=NOW - age, details={"k": k}, group_id=group.id if group else None,
                          user_agent="Firefox"))
    db.commit()
    return [e.id for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id))]


def test_events_go_in_batches_in_order_and_carry_on_from_their_place(db, siem_on):
    g = make_group(db, "Campus A", "a.test")
    ids = _events(db, 12, group=g)
    _events(db, 1, age=timedelta(seconds=10))  # too new: a transaction that started earlier may still commit
    sink = Recorder()
    assert siem.export(db, sink, NOW) == 12
    assert [len(b) for b in sink.batches] == [5, 5, 2]
    sent = [e for b in sink.batches for e in b]
    assert [e["id"] for e in sent] == ids
    e = sent[0]
    assert e["service"] == "pdf-remediation" and e["action"] == "login" and e["outcome"] == "success"
    assert e["actor"]["email"] == "p0@a.test" and e["group"] == {"id": str(g.id), "name": "Campus A"}
    assert e["ip"] == "198.51.100.4" and e["user_agent"] == "Firefox" and e["details"] == {"k": 0}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", e["time"])
    assert siem.state(db)["after"] == ids[-1]
    # Later: only what's new
    sink.batches.clear()
    more = _events(db, 2)[-2:]
    assert siem.export(db, sink, NOW + timedelta(minutes=5)) == 3  # the young one is old enough now
    assert [e["id"] for b in sink.batches for e in b][-2:] == more


def test_a_failure_keeps_the_place_and_is_tried_again_later(db, siem_on):
    ids = _events(db, 3)
    sink = Recorder(fail=2)
    assert siem.export(db, sink, NOW) == 0
    st = siem.state(db)
    assert st["after"] == 0 and st["failures"] == 1 and "collector away" in st["last_error"]
    assert siem.export(db, sink, NOW + timedelta(seconds=10)) == 0 and sink.fail == 1  # waiting: not even tried
    assert siem.export(db, sink, NOW + timedelta(seconds=31)) == 0 and sink.fail == 0
    assert siem.state(db)["failures"] == 2  # the wait doubles, to a minute
    assert siem.export(db, sink, NOW + timedelta(seconds=60)) == 0 and not sink.batches
    assert siem.export(db, sink, NOW + timedelta(seconds=92)) == 3
    assert [e["id"] for e in sink.batches[0]] == ids
    st = siem.state(db)
    assert st["failures"] == 0 and st["retry_at"] is None and st["after"] == ids[-1]


def test_syslog_messages_are_rfc5424_with_octet_counting():
    base = {"id": 7, "time": "2026-10-21T12:00:00.000Z", "service": "pdf-remediation", "outcome": "success",
            "actor": {}, "group": {}, "target": {}, "ip": None, "user_agent": None, "details": {}}
    ok = siem.syslog_message({**base, "action": "file.accessed"}, "pdf.example.edu")
    head, body = ok.split(b" {", 1)
    assert head == b"<110>1 2026-10-21T12:00:00.000Z pdf.example.edu pdf-remediation - file.accessed -"
    assert json.loads(b"{" + body)["id"] == 7
    assert siem.syslog_message({**base, "action": "login", "outcome": "invalid_code"}, "h").startswith(b"<109>1 ")
    assert siem.syslog_message({**base, "action": "security.alert", "outcome": "alert"}, "h").startswith(b"<108>1 ")
    framed = siem.syslog_frame(ok)
    n, rest = framed.split(b" ", 1)
    assert int(n) == len(rest) == len(ok)


def _tls_listener(tmp_path):
    """A TLS listener for localhost with a certificate of its own; returns (port, CA PEM, received, thread)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    (tmp_path / "cert.pem").write_text(pem)
    (tmp_path / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                          serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    # No TLS 1.3 session tickets: the server writes them at the end of its handshake, and the sink sends and closes
    # at once, so on a busy machine that write met a closed socket and the batch already received was lost.
    ctx.num_tickets = 0
    srv = socket.create_server(("127.0.0.1", 0))
    srv.settimeout(0.5)
    received: list[bytes] = []
    deadline = time.monotonic() + 10

    def serve():
        # Until something arrives: a refused handshake (the untrusted send) doesn't end it
        while not received and time.monotonic() < deadline:
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            conn.settimeout(5)
            data = b""
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    while chunk := tls.recv(65536):
                        data += chunk
            except (ssl.SSLError, OSError):  # incl. a sender closing without TLS's own goodbye, as many do
                pass
            if data:
                received.append(data)
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv.getsockname()[1], pem.replace("\n", "\\n"), received, t


def test_syslog_over_tls_to_a_real_listener(tmp_path):
    port, ca, received, t = _tls_listener(tmp_path)
    batch = [{"id": i, "time": "2026-10-21T12:00:00.000Z", "service": "pdf-remediation", "action": "login",
              "outcome": "success", "actor": {"email": f"p{i}@a.test"}, "group": {}, "target": {}, "ip": None,
              "user_agent": None, "details": {}} for i in (1, 2)]
    # Not trusted without the collector's CA
    with pytest.raises(ssl.SSLError):
        siem.SyslogSink("localhost", port, tls=True).send(batch)
    siem.SyslogSink("localhost", port, tls=True, ca=ca).send(batch)
    t.join(timeout=15)
    assert received, "the listener got nothing"
    data = received[-1]
    messages = []
    while data:
        n, data = data.split(b" ", 1)
        messages.append(data[:int(n)])
        data = data[int(n):]
    assert [json.loads(b"{" + m.split(b" {", 1)[1])["id"] for m in messages] == [1, 2]


def _http_listener():
    got: list[tuple[dict, bytes]] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got.append((dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def test_https_as_json_lines_or_splunk_hec():
    srv, got = _http_listener()
    url = f"http://127.0.0.1:{srv.server_port}/collect"
    batch = [{"id": 1, "time": "2026-10-21T12:00:00.000Z", "action": "login"},
             {"id": 2, "time": "2026-10-21T12:00:01.000Z", "action": "logout"}]
    try:
        siem.HttpsSink(url, "tok", "lines").send(batch)
        siem.HttpsSink(url, "tok", "splunk").send(batch)
    finally:
        srv.shutdown()
    (h1, b1), (h2, b2) = got
    assert h1["Authorization"] == "Bearer tok" and h1["Content-Type"] == "application/x-ndjson"
    assert [json.loads(line)["id"] for line in b1.decode().splitlines()] == [1, 2]
    assert h2["Authorization"] == "Splunk tok"
    hec = [json.loads(x) for x in re.findall(r"\{\"time\".*?\}\}", b2.decode())]
    assert [x["event"]["id"] for x in hec] == [1, 2] and hec[0]["sourcetype"] == "_json"
    assert hec[0]["time"] == datetime(2026, 10, 21, 12, tzinfo=timezone.utc).timestamp()


def test_the_pull_api_needs_its_own_token(db, make_client, monkeypatch):
    ids = _events(db, 3)
    c = make_client()
    assert c.get("/api/v1/admin/audit/export").status_code == 404  # no token set: no such address
    monkeypatch.setattr(get_portal_settings(), "siem_pull_token", "pull-secret-1234")
    assert c.get("/api/v1/admin/audit/export", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/v1/admin/audit/export?limit=2", headers={"Authorization": "Bearer pull-secret-1234"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert [json.loads(x)["id"] for x in r.text.splitlines()] == ids[:2]
    nxt = r.headers["X-Next-After"]
    r = c.get(f"/api/v1/admin/audit/export?after={nxt}", headers={"Authorization": "Bearer pull-secret-1234"})
    assert [json.loads(x)["id"] for x in r.text.splitlines()] == ids[2:]
    r = c.get(f"/api/v1/admin/audit/export?after={ids[-1]}", headers={"Authorization": "Bearer pull-secret-1234"})
    assert r.text == "" and r.headers["X-Next-After"] == str(ids[-1])


def test_an_hour_behind_is_alerted_and_the_status_shows_it(db, make_client, siem_on):
    _events(db, 2, age=timedelta(hours=2))
    sink = Recorder(fail=1)
    siem.export(db, sink, NOW)
    found = alerts.conditions(db, now=NOW)
    assert "siem_behind" in found and "collector away" in found["siem_behind"]
    make_user(db, "root@admin.test", None, role="super_admin")
    db.commit()
    c = make_client()
    login(c, "root@admin.test")
    st = c.get("/api/v1/admin/audit/siem").json()
    assert st["mode"] == "syslog" and st["failures"] == 1 and st["behind_seconds"] > 3600
    # Sent: no longer behind
    siem.export(db, Recorder(), NOW + timedelta(minutes=5))
    assert "siem_behind" not in alerts.conditions(db, now=NOW + timedelta(minutes=5))
