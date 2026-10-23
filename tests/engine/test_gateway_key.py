"""#457: deploy/k8s/gateway_key.py, which deploy.sh runs inside the gateway's pod to give the engine its own key: limited
to the listed models (and a budget, when given), made, updated, checked and revoked with the master key, which it
reads from the pod's environment. Against a stand-in gateway."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "gateway_key.py"


@pytest.fixture
def gateway():
    seen: list[tuple[str, str, dict, str]] = []
    known = {"sk-old"}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body=None):
            self.send_response(code)
            self.end_headers()
            self.wfile.write(json.dumps(body or {}).encode())

        def do_GET(self):  # noqa: N802
            seen.append(("GET", self.path, {}, self.headers.get("Authorization")))
            self._reply(200 if any(k in self.path for k in known) else 404)

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("POST", self.path, body, self.headers.get("Authorization")))
            self._reply(200, {"key": "sk-new"} if self.path == "/key/generate" else {})

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def run(command: str, args: dict) -> tuple[int, str]:
        env = {**os.environ, "GATEWAY_URL": f"http://127.0.0.1:{server.server_port}", "LITELLM_MASTER_KEY": "sk-master"}
        p = subprocess.run([sys.executable, str(SCRIPT), command], input=json.dumps(args), env=env,
                           capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout.strip()

    yield run, seen
    server.shutdown()


def test_a_new_key_is_limited_to_the_models_and_made_with_the_master_key(gateway):
    run, seen = gateway
    code, out = run("generate", {"models": ["commercial-small-vision", "open-judge"]})
    assert (code, out) == (0, "sk-new")
    method, path, body, auth = seen[-1]
    assert (method, path, auth) == ("POST", "/key/generate", "Bearer sk-master")
    assert body["models"] == ["commercial-small-vision", "open-judge"] and body["key_alias"] == "pdfrem-engine"
    assert "max_budget" not in body
    run("generate", {"models": ["a"], "max_budget": 25})
    assert seen[-1][2]["max_budget"] == 25.0 and seen[-1][2]["budget_duration"] == "30d"


def test_a_known_key_is_updated_and_an_unknown_one_reported(gateway):
    run, seen = gateway
    assert run("check", {"key": "sk-old"})[0] == 0
    assert run("check", {"key": "sk-gone"})[0] == 1
    assert run("update", {"key": "sk-old", "models": ["trial-a-vision"]})[0] == 0
    assert seen[-1][1:3] == ("/key/update", {"key": "sk-old", "models": ["trial-a-vision"]})
    assert run("delete", {"key": "sk-old"})[0] == 0
    assert seen[-1][1:3] == ("/key/delete", {"keys": ["sk-old"]})
    assert all(auth == "Bearer sk-master" for *_, auth in seen)
