"""#460: the Ingresses for either controller, as scripts/deploy.sh renders them (in a copy of the repository's deploy
files, with settings of its own: a real deploy/k8s/deploy.env is never touched)."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCES = "173.245.48.0/20,103.21.244.0/22"

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("perl")), reason="needs bash and perl")


def _render(tmp_path: Path, controller: str, frontend: str, pattern: str = "6*.yaml", **extra: str
            ) -> dict[str, list[dict]]:
    """The rendered manifests matching the pattern (by default the web app's and the API origin's), by file."""
    work = tmp_path / f"{controller}-{frontend}-{len(list(tmp_path.iterdir()))}"
    shutil.copytree(ROOT / "scripts", work / "scripts")
    shutil.copytree(ROOT / "deploy", work / "deploy", ignore=shutil.ignore_patterns("*.env", ".rendered", ".last-build"))
    shutil.copytree(ROOT / "config", work / "config")
    k8s = work / "deploy" / "k8s"
    settings = {"STORAGE_CLASS": "longhorn", "SHARED_STORAGE_CLASS": "nfs", "ENGINE_TAG": "t", "PORTAL_TAG": "t",
                "WEB_TAG": "t", "INGRESS_CONTROLLER": controller, "FRONTEND": frontend, "API_ORIGIN_HOST": "api.example.edu",
                "API_ORIGIN_SOURCE_CIDR": "10.42.0.0/16", "API_ORIGIN_ALLOWED_SOURCES": SOURCES, **extra}
    lines = [ln for ln in (k8s / "deploy.env.example").read_text().splitlines()
             if ln.split("=", 1)[0] not in settings]
    (k8s / "deploy.env").write_text("\n".join(lines + [f"{k}={v}" for k, v in settings.items()]) + "\n")
    (k8s / "engine.env").write_text("")
    (k8s / "portal.env").write_text("")
    run = subprocess.run(["bash", "scripts/deploy.sh", "render"], cwd=work, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    return {f.name: [d for d in yaml.safe_load_all(f.read_text()) if d]
            for f in sorted((k8s / ".rendered").glob(pattern))}


def _ingress(docs: list[dict]) -> dict:
    return next(d for d in docs if d["kind"] == "Ingress")


def test_ingress_nginx_keeps_its_annotations(tmp_path):
    out = _render(tmp_path, "nginx", "cluster", CERT_ISSUER="letsencrypt")
    assert list(out) == ["60-web.yaml"]
    web = _ingress(out["60-web.yaml"])
    assert web["spec"]["ingressClassName"] == "nginx"
    assert web["metadata"]["annotations"] == {
        "pdfrem/ingress-controller": "nginx", "nginx.ingress.kubernetes.io/proxy-body-size": "100m",
        "nginx.ingress.kubernetes.io/proxy-request-buffering": "off",
        "nginx.ingress.kubernetes.io/proxy-read-timeout": "300", "nginx.ingress.kubernetes.io/proxy-send-timeout": "300",
        "cert-manager.io/cluster-issuer": "letsencrypt"}
    api = _render(tmp_path, "nginx", "worker")
    assert list(api) == ["61-api-origin.yaml"]  # no Traefik objects
    origin = _ingress(api["61-api-origin.yaml"])
    assert origin["spec"]["ingressClassName"] == "nginx"
    assert origin["metadata"]["annotations"]["nginx.ingress.kubernetes.io/whitelist-source-range"] == SOURCES


def test_traefik_gets_its_class_and_the_api_origins_allow_list_as_a_middleware(tmp_path):
    web = _ingress(_render(tmp_path, "traefik", "cluster")["60-web.yaml"])
    assert web["spec"]["ingressClassName"] == "traefik"
    assert web["metadata"]["annotations"] == {"pdfrem/ingress-controller": "traefik"}  # no nginx ones
    out = _render(tmp_path, "traefik", "worker", INGRESS_CLASS="campus-traefik")
    assert list(out) == ["61-api-origin.yaml", "62-traefik-api-origin.yaml"]
    origin = _ingress(out["61-api-origin.yaml"])
    assert origin["spec"]["ingressClassName"] == "campus-traefik"  # a class of the cluster's own
    middleware = out["62-traefik-api-origin.yaml"][0]
    assert (middleware["apiVersion"], middleware["kind"]) == ("traefik.io/v1alpha1", "Middleware")
    assert middleware["spec"] == {"ipAllowList": {"sourceRange": SOURCES.split(",")}}
    ns, name = middleware["metadata"]["namespace"], middleware["metadata"]["name"]
    assert origin["metadata"]["annotations"]["traefik.ingress.kubernetes.io/router.middlewares"] == \
        f"{ns}-{name}@kubernetescrd"
    assert not any(k.startswith("nginx.") for k in origin["metadata"]["annotations"])


def test_an_unknown_controller_is_refused(tmp_path):
    with pytest.raises(AssertionError, match="INGRESS_CONTROLLER must be nginx or traefik"):
        _render(tmp_path, "haproxy", "cluster")
