"""deploy/k8s/env2secret.py turns env files into a Secret with the keys its pod reads: a pattern keeps some, a
pattern leaves some out, a later file's value wins. With every key in every secret (how it was), an engine worker
held the gateway's master key, the OpenRouter key and the Adobe secret it never used."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("env2secret", ROOT / "deploy/k8s/env2secret.py")
env2secret = importlib.util.module_from_spec(spec)
spec.loader.exec_module(env2secret)


@pytest.fixture
def files(tmp_path):
    (tmp_path / "engine.env").write_text(
        "# the engine's\nREDIS_PASSWORD=r\nSERVICE_API_KEY=s\nGATEWAY_API_KEY=g\nLITELLM_MASTER_KEY=g\n"
        "LITELLM_DB_PASSWORD=l\nUI_PASSWORD=u\nOPENROUTER_API_KEY=o\nLOCAL_AI_BASE_URL='http://ai:8080/v1'\n"
        "ADOBE_ENABLED=false\nADOBE_CLIENT_ID=a\nADOBE_CLIENT_SECRET=b\nOCR_LANGUAGE=eng # a comment\n")
    (tmp_path / "portal.env").write_text("PORTAL_DB_PASSWORD=p\nPOSTGRES_ADMIN_PASSWORD=q\nPORTAL_SECRET_KEY=k\n")
    return tmp_path


def _run(capsys, *argv):
    env2secret.main(list(argv))
    return json.loads(capsys.readouterr().out)


def test_drop_leaves_the_gateway_keys_and_adobe_out_of_the_engine_secret(files, capsys):
    out = _run(capsys, "pdfrem-engine-env", "ns", str(files / "engine.env"),
               "--drop", "^(LITELLM_|UI_|LOCAL_|OPENROUTER_)|^ADOBE_CLIENT_")
    assert out["kind"] == "Secret" and out["metadata"] == {
        "name": "pdfrem-engine-env", "namespace": "ns", "labels": {"app.kubernetes.io/part-of": "pdf-remediation"}}
    assert out["stringData"] == {"REDIS_PASSWORD": "r", "SERVICE_API_KEY": "s", "GATEWAY_API_KEY": "g",
                                 "ADOBE_ENABLED": "false", "OCR_LANGUAGE": "eng"}


def test_keys_keeps_only_the_matching_ones_across_files(files, capsys):
    out = _run(capsys, "pdfrem-db-env", "ns", str(files / "engine.env"), str(files / "portal.env"),
               "--keys", "^(POSTGRES_ADMIN_PASSWORD|PORTAL_DB_PASSWORD|LITELLM_DB_PASSWORD)$")
    assert out["stringData"] == {"LITELLM_DB_PASSWORD": "l", "PORTAL_DB_PASSWORD": "p", "POSTGRES_ADMIN_PASSWORD": "q"}
    out = _run(capsys, "pdfrem-adobe-env", "ns", str(files / "engine.env"), "--keys", "^ADOBE_CLIENT_")
    assert out["stringData"] == {"ADOBE_CLIENT_ID": "a", "ADOBE_CLIENT_SECRET": "b"}


def test_without_a_pattern_every_key_is_kept_and_quotes_come_off(files, capsys):
    out = _run(capsys, "x", "ns", str(files / "engine.env"))
    assert len(out["stringData"]) == 12 and out["stringData"]["LOCAL_AI_BASE_URL"] == "http://ai:8080/v1"


def test_a_later_file_wins(files, capsys):
    (files / "over.env").write_text("REDIS_PASSWORD=later\n")
    out = _run(capsys, "x", "ns", str(files / "engine.env"), str(files / "over.env"), "--keys", "^REDIS_")
    assert out["stringData"] == {"REDIS_PASSWORD": "later"}
