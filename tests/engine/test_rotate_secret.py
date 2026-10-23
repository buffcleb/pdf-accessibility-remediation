"""#454: scripts/rotate-secret.sh, which gives one secret a new value in deploy/k8s's settings files (the database role
first, for its passwords), never printing it, then deploys. Run with --no-deploy against a folder of stand-in files and
a stand-in kubectl that records what it was asked and what it was sent."""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rotate-secret.sh"

ENGINE = """# engine settings
REDIS_PASSWORD=oldredis
SERVICE_API_KEY=oldservice
LITELLM_MASTER_KEY=sk-oldmaster
GATEWAY_API_KEY=sk-engineown
LITELLM_DB_PASSWORD=oldlitellm
OPENROUTER_API_KEY=oldrouter
"""
PORTAL = """PORTAL_ENGINE_API_KEY=oldservice
PORTAL_DB_PASSWORD=oldportal
PORTAL_SECRET_KEY=oldsecret
PORTAL_S3_ACCESS_KEY=portal-old
PORTAL_S3_SECRET_KEY=olds3
PORTAL_S3_SSE_KEK=oldkek
"""


@pytest.fixture
def k8s(tmp_path):
    folder = tmp_path / "k8s"
    folder.mkdir()
    (folder / "engine.env").write_text(ENGINE)
    (folder / "portal.env").write_text(PORTAL)
    (folder / "deploy.env").write_text("NAMESPACE=pdf-test\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/kubectl.args\ncat >> {tmp_path}/kubectl.stdin\n'
                       f'exit "${{KUBECTL_EXIT:-0}}"\n')
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)

    def run(*args, stdin="", kubectl_exit=0):
        env = {**os.environ, "ROTATE_K8S_DIR": str(folder), "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "KUBECTL_EXIT": str(kubectl_exit)}
        p = subprocess.run(["bash", str(SCRIPT), *args, "--no-deploy"], input=stdin, env=env, capture_output=True,
                           text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def value(file, key):
        m = re.search(rf"(?m)^{key}=(.*)$", (folder / file).read_text())
        return m.group(1) if m else None

    def kube():
        args, sent = tmp_path / "kubectl.args", tmp_path / "kubectl.stdin"
        return (args.read_text() if args.exists() else ""), (sent.read_text() if sent.exists() else "")

    yield run, value, kube


def test_a_password_gets_a_new_value_and_nothing_else_changes(k8s):
    run, value, _ = k8s
    code, out = run("REDIS_PASSWORD")
    assert code == 0, out
    new = value("engine.env", "REDIS_PASSWORD")
    assert re.fullmatch(r"[0-9a-f]{48}", new)
    assert new not in out  # never printed
    assert value("engine.env", "SERVICE_API_KEY") == "oldservice"
    assert value("engine.env", "OPENROUTER_API_KEY") == "oldrouter"


def test_the_engines_api_key_changes_in_both_files_together(k8s):
    run, value, _ = k8s
    assert run("SERVICE_API_KEY")[0] == 0
    new = value("engine.env", "SERVICE_API_KEY")
    assert new != "oldservice" and value("portal.env", "PORTAL_ENGINE_API_KEY") == new


def test_a_database_password_is_set_on_its_role_first_and_never_as_an_argument(k8s):
    run, value, kube = k8s
    code, out = run("LITELLM_DB_PASSWORD")
    assert code == 0, out
    new = value("engine.env", "LITELLM_DB_PASSWORD")
    args, sent = kube()
    assert "-n pdf-test exec -i statefulset/postgres -- psql" in args and new not in args
    assert sent == f"ALTER ROLE litellm PASSWORD '{new}';\n"


def test_a_role_the_database_refuses_leaves_the_old_password(k8s):
    run, value, _ = k8s
    code, out = run("PORTAL_DB_PASSWORD", kubectl_exit=1)
    assert code != 0 and "nothing changed" in out
    assert value("portal.env", "PORTAL_DB_PASSWORD") == "oldportal"


def test_a_typed_value_is_asked_twice_and_kept_quoted(k8s):
    run, value, _ = k8s
    assert run("OPENROUTER_API_KEY", "--typed", stdin="sk-or-new #1\nsk-or-new #1\n")[0] == 0
    assert value("engine.env", "OPENROUTER_API_KEY") == '"sk-or-new #1"'
    code, out = run("OPENROUTER_API_KEY", "--typed", stdin="one\ntwo\n")
    assert code != 0 and "didn't match" in out
    assert value("engine.env", "OPENROUTER_API_KEY") == '"sk-or-new #1"'


def test_the_file_stores_keys_together_and_its_encryption_key_never(k8s):
    run, value, _ = k8s
    assert run("PORTAL_S3_SECRET_KEY")[0] == 0
    assert value("portal.env", "PORTAL_S3_ACCESS_KEY").startswith("portal-")
    assert value("portal.env", "PORTAL_S3_ACCESS_KEY") != "portal-old"
    assert value("portal.env", "PORTAL_S3_SECRET_KEY") != "olds3"
    code, out = run("PORTAL_S3_SSE_KEK")
    assert code != 0 and "can't simply be changed" in out
    assert value("portal.env", "PORTAL_S3_SSE_KEK") == "oldkek"


def test_the_master_key_isnt_changed_while_the_engine_still_uses_it(k8s, tmp_path):
    run, value, _ = k8s
    engine = tmp_path / "k8s" / "engine.env"
    engine.write_text(engine.read_text().replace("GATEWAY_API_KEY=sk-engineown", "GATEWAY_API_KEY=sk-oldmaster"))
    code, out = run("LITELLM_MASTER_KEY")
    assert code != 0 and "still uses the master key" in out
    engine.write_text(ENGINE)
    assert run("LITELLM_MASTER_KEY")[0] == 0
    assert re.fullmatch(r"sk-[0-9a-f]{48}", value("engine.env", "LITELLM_MASTER_KEY"))


def test_a_new_signing_key_goes_first_and_finish_keeps_only_it(k8s):
    from cryptography.hazmat.primitives import serialization

    run, value, _ = k8s
    pem = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.+?-----END [A-Z ]*PRIVATE KEY-----", re.S)

    def keys():
        raw = value("portal.env", "PORTAL_JWT_PRIVATE_KEYS").strip('"').replace("\\n", "\n")
        return pem.findall(raw)

    assert run("PORTAL_JWT_PRIVATE_KEYS")[0] == 0
    (first,) = keys()
    assert run("PORTAL_JWT_PRIVATE_KEYS")[0] == 0
    newest, older = keys()
    assert older == first and newest != first
    key = serialization.load_pem_private_key(newest.encode(), password=None)
    assert key.curve.name == "secp256r1"
    assert run("PORTAL_JWT_PRIVATE_KEYS", "--finish")[0] == 0
    assert keys() == [newest]
    code, out = run("PORTAL_JWT_PRIVATE_KEYS", "--finish")
    assert code != 0 and "only one signing key" in out


def test_a_secret_not_made_here_points_to_typed(k8s):
    run, _, _ = k8s
    code, out = run("ADOBE_CLIENT_SECRET")
    assert code != 0 and "--typed" in out
