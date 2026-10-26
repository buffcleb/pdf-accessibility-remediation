#!/usr/bin/env python3
"""Writes docs/operations/settings-reference.md: every setting, with its default and what it does, from the code.

    python scripts/settings_reference.py          write the page
    python scripts/settings_reference.py --check  exit 1 when the page is out of date or a setting has no description

Where each description comes from:
- the engine's and the portal's settings: the docstring under each field in app/config.py and portal/config.py;
- deploy.env: the comment above each line of deploy/k8s/deploy.env.example. A comment block over a single line
  describes it; where a block is over several lines, each has a bullet of its own ("# - KEY: what it does");
- everything else (the AI gateway's config/litellm.yaml, the passwords deploy.sh init makes, variables the code reads
  with os.environ, the web container's, the Compose files', the scripts'): the tables below. Each is checked against
  what the code reads, so a new one without a description fails the test (tests/engine/test_settings_reference.py).

Secrets are never shown with a value: their default says "generated" (deploy.sh init makes it) or "set when used".
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/operations/settings-reference.md"
DEPLOY_ENV = ROOT / "deploy/k8s/deploy.env.example"
DEPLOY_SH = ROOT / "scripts/deploy.sh"
LITELLM = ROOT / "config/litellm.yaml"
COMPOSE = [ROOT / "docker-compose.yml", ROOT / "docker-compose.portal.yml"]
# Code that reads the environment itself (os.environ.get / os.getenv) rather than through a settings class
DIRECT_CODE = ["app", "portal", "docker/formula", "docker/audiveris"]
WEB_FILES = [ROOT / "Dockerfile.web", *sorted((ROOT / "docker/web").glob("*"))]
# The operator's scripts: what they read from the shell environment (dev-only ones such as a11y.sh, regression.sh
# and publish.sh are described in their own headers)
SCRIPTS = ["deploy.sh", "build.sh", "backup.sh", "restore.sh", "rotate-secret.sh", "deploy-worker.sh"]

GENERATED = "generated"
WHEN_USED = "set when used"
SECRET_NAME = re.compile(r"(_KEY|_SECRET|_PASSWORD|_TOKEN|_KEK|_PRIVATE_KEYS|^LITELLM_MASTER_KEY)$")
NOT_SECRET = {"PORTAL_TURNSTILE_SITE_KEY",  # published in the page the bot check runs on
              "ALLOW_NO_API_KEY", "INGRESS_TLS_SECRET"}

# --- the engine's and the portal's settings: grouped, in the order of their config.py within a group -----------
# (title, link for more, the fields in it: a name, or a prefix ending in "_")
ENGINE_GROUPS = [
    ("Service and limits", "", ["redis_url", "celery_result_backend", "data_dir", "public_base_url",
                                         "service_api_key", "allow_no_api_key", "max_upload_mb", "max_pages",
                                         "job_ttl_hours", "task_time_limit_s", "worker_max_memory_mb", "page_workers",
                                         "page_workers_min_pages"]),
    ("Remediation and veraPDF", "",
     ["default_language", "pdfua_part", "verapdf_", "raster_dpi", "destructive_font_path", "substitute_font_dir"]),
    ("OCR", "ai-models.md#ocr-correction", ["ocr_"]),
    ("The AI gateway", "ai-models.md#the-gateway", ["gateway_", "ai_gateway_only"]),
    ("How much the AI is asked", "limits.md#what-the-ai-is-asked", ["ai_", "allow_mock_provider"]),
    ("Music and formulas", "limits.md#music", ["music_", "omr_", "formula_"]),
    ("AI providers called directly (development)", "ai-models.md#without-the-gateway-development-only",
     ["openrouter_", "classifier_jev_model", "ollama_", "localai_"]),
    ("Verification", "ai-models.md#verification-settings", ["verify_"]),
    ("Adobe Autotag", "adobe-autotag.md", ["adobe_"]),
]
ENGINE_LINKS = {
    "max_upload_mb": "limits.md#uploading", "max_pages": "limits.md#uploading",
    "job_ttl_hours": "limits.md#queues-and-time", "task_time_limit_s": "limits.md#queues-and-time",
    "worker_max_memory_mb": "limits.md#long-documents-what-to-raise-together",
    "page_workers": "limits.md#long-documents-what-to-raise-together",
    "page_workers_min_pages": "limits.md#long-documents-what-to-raise-together",
    "verapdf_path": "configuration.md#verapdf-service", "verapdf_flavours": "configuration.md#verapdf-service",
    "verapdf_timeout_s": "configuration.md#verapdf-service", "verapdf_cli_heap": "configuration.md#verapdf-service",
    "service_api_key": "secret-rotation.md#the-engines-api-key",
    "gateway_api_key": "secret-rotation.md#the-ai-gateways-keys",
    "verapdf_url": "configuration.md#verapdf-service",
    "formula_ocr_enabled": "limits.md#formula-reader", "formula_ocr_timeout_s": "limits.md#formula-reader",
    "formula_ocr_max": "limits.md#formula-reader",
    "classifier_jev_model": "ai-models.md#the-kind-of-document-classifier",
    "ai_notation_enabled": "ai-models.md#specialist-notation", "ai_notation_max": "ai-models.md#specialist-notation",
    "verify_ai_max_items": "limits.md#checking-the-result", "verify_max_pages": "limits.md#checking-the-result",
}
PORTAL_GROUPS = [
    ("Database and addresses", "configuration.md#the-portal", ["database_url", "redis_url", "cors_origins", "app_url"]),
    ("Sign-in", "secret-rotation.md#session-keys",
     ["jwt_", "access_token_minutes", "cookie_", "refresh_cookie_name", "secret_key", "bootstrap_super_admins",
      "otp_"]),
    ("Bot check on sign-in", "../deployment/cloudflare.md#a-bot-check-on-sign-in", ["turnstile_"]),
    ("Rate limits", "limits.md#reports-files-and-sign-in", ["rate_"]),
    ("Email", "email-and-branding.md#email", ["mail_", "smtp_", "ses_", "plunk_", "cloudflare_"]),
    ("Proxies and the caller's address", "configuration.md#the-portal",
     ["trust_proxy_headers", "trusted_proxies", "client_ip_header", "trusted_proxy_hops"]),
    ("Uploads", "limits.md#uploading", ["pdfcheck_"]),
    ("Malware scanning", "malware-scanning.md", ["malware_", "clamd_"]),
    ("Object storage", "secret-rotation.md#the-file-stores-keys", ["storage_backend", "s3_"]),
    ("The engine", "configuration.md#scaling-with-the-queue", ["engine_", "review_findings_max",
                                                               "gateway_admin_url"]),
    ("Monitoring and backups", "monitoring.md#prometheus-and-grafana",
     ["metrics_enabled", "worker_metrics_port", "grafana_url", "prometheus_url", "backup_expected"]),
    ("The audit log to a SIEM", "siem.md", ["siem_"]),
    ("Background worker", "", ["worker_"]),
]
PORTAL_LINKS = {
    "review_findings_max": "limits.md#checking-the-result",
    "engine_api_key": "secret-rotation.md#the-engines-api-key",
    "gateway_admin_url": "ai-models.md#the-gateway",
    "backup_expected": "backups-and-migrations.md#nightly-backups-on-the-cluster",
    "s3_sse": "", "storage_backend": "",
}

# --- the AI gateway: what config/litellm.yaml reads (os.environ/NAME), and LiteLLM's own -------------------------
GATEWAY = {
    "LITELLM_MASTER_KEY": (GENERATED, "LiteLLM's admin key (starts with `sk-`). It makes the engine's own key and "
                                      "never leaves the gateway on a cluster"),
    "LITELLM_DATABASE_URL": ("set by the compose file or manifests", "The gateway's own PostgreSQL (keys, spend)"),
    "LITELLM_DB_PASSWORD": (GENERATED, "The password of the gateway's database"),
    "UI_USERNAME": ("`admin` in `.env.example`", "The shared sign-in to the gateway's admin UI (with `GATEWAY_SHARED_LOGIN` on, on a "
                               "cluster)"),
    "UI_PASSWORD": (GENERATED, "The shared admin UI sign-in's password"),
    "LOCAL_AI_BASE_URL": ("empty", "The local OpenAI-compatible model server (LocalAI, vLLM, Ollama's `/v1`) the "
                                   "local models run on"),
    "LOCAL_AI_API_KEY": (WHEN_USED, "The local model server's key, if it has one"),
    "LOCAL_VISION_MODEL": ("empty", "The model behind `local-vision`, as LiteLLM names it (for example "
                                    "`openai/qwen3-vl-8b-instruct`)"),
    "LOCAL_JUDGE_MODEL": ("empty", "The model behind `local-judge`, the local review model"),
    "OPENROUTER_API_KEY": (WHEN_USED, "OpenRouter key, for the gateway's cloud models (and the engine's direct "
                                      "calls)"),
    "AWS_ACCESS_KEY_ID": (WHEN_USED, "An AWS access key limited to `bedrock:InvokeModel` on the gateway's Bedrock "
                                     "models"),
    "AWS_SECRET_ACCESS_KEY": (WHEN_USED, "That access key's secret"),
    "AWS_REGION_NAME": ("`us-east-1`", "The region of the Bedrock models' `us.` profiles"),
}
GATEWAY_LINKS = {"AWS_ACCESS_KEY_ID": "ai-models.md#aws-bedrock", "AWS_SECRET_ACCESS_KEY": "ai-models.md#aws-bedrock",
                 "AWS_REGION_NAME": "ai-models.md#aws-bedrock",
                 "LITELLM_MASTER_KEY": "secret-rotation.md#the-ai-gateways-keys"}

# --- passwords and keys the services' settings don't read: used by the manifests and Compose files --------------
SECRETS = {
    "REDIS_PASSWORD": ("engine.env", "The engine's Redis (the job queue)"),
    "PORTAL_REDIS_PASSWORD": ("portal.env", "The portal's own Redis, apart from the engine's (#472)"),
    "PORTAL_DB_PASSWORD": ("portal.env, .env.portal", "The portal database's owner: migrations and backups only"),
    "PORTAL_APP_DB_PASSWORD": ("portal.env", "The portal's own rows-only database account, `portal_app` (#337)"),
    "POSTGRES_ADMIN_PASSWORD": ("portal.env", "The PostgreSQL server's admin account"),
    "PORTAL_S3_SSE_KEK": ("portal.env, .env.portal", "SeaweedFS's key for encryption at rest (32 random bytes as "
                                                     "hex)"),
    "MONITORING_DB_PASSWORD": ("portal.env", "The Postgres exporter's read-only account (#397)"),
    "GRAFANA_ADMIN_PASSWORD": ("portal.env", "Grafana's own admin sign-in; nobody uses it (super admins come "
                                             "through the portal)"),
    "GRAFANA_SECRET_KEY": ("portal.env", "The key Grafana signs its cookies with"),
}
SECRET_LINKS = {"PORTAL_S3_SSE_KEK": "secret-rotation.md#the-file-stores-encryption-key",
                "GRAFANA_ADMIN_PASSWORD": "secret-rotation.md#grafana", "GRAFANA_SECRET_KEY": "secret-rotation.md#grafana"}
SECRETS_DEFAULT_LINK = "secret-rotation.md#every-secret"

# --- read with os.environ by the code, not through a settings class --------------------------------------------
DIRECT = {
    "WORKER_METRICS": "`off` stops an engine worker serving its metrics for Prometheus (#397)",
    "WORKER_METRICS_PORT": "The port an engine worker serves its metrics on",
    "WORKER_ALIVE_FILE": "The file an engine worker touches while it's working, for its liveness check",
    "MATHCAT_BIN": "The MathCAT command that speaks formulas (found on the `PATH` when empty)",
    "PORTAL_ENABLE_DOCS": "Interactive API docs at `/api/v1/docs`; leave off in production",
    "PORTAL_WORKER_ALIVE_FILE": "The file the portal worker touches while it's working, for its liveness check",
    "BACKUP_S3_ENDPOINT_URL": "A non-AWS S3 service backups are uploaded to (`backup.sh --upload`); empty for AWS",
    "BACKUP_S3_REGION": "The backup bucket's region",
    "BACKUP_S3_ACCESS_KEY": "The backup bucket's access key; empty: the standard AWS variables or the machine's role",
    "BACKUP_S3_SECRET_KEY": "The backup bucket's secret key",
    "AWS_DEFAULT_REGION": "The backup bucket's region when `BACKUP_S3_REGION` isn't set",
    "FAKE_ADOBE_THROTTLE_EVERY": "Tests only: the fake Adobe answers \"too many requests\" every this many calls",
    "FAKE_ADOBE_QUOTA": "Tests only: the fake Adobe says the transactions have run out",
    "FAKE_ADOBE_POLLS": "Tests only: status checks before the fake Adobe says a part is done",
    "FAKE_ADOBE_FAIL": "Tests only: the fake Adobe fails the way named",
    "REDIS_URL": "The music and formula readers' Redis (the engine's)",
    "METRICS_PORT": "The music and formula readers' metrics port (0: none)",
    "FORMULA_RESULT_TTL_S": "How long the formula reader keeps an answer for the engine to collect, in seconds",
    "AUDIVERIS_BIN": "The music reader's Audiveris command",
    "OMR_READ_TIMEOUT_S": "How long the music reader gives Audiveris for one picture, in seconds",
    "OMR_PAGE_TIMEOUT_S": "For a whole score: this much longer for each of its pages, in seconds",
    "OMR_RESULT_TTL_S": "How long the music reader keeps an answer for the engine to collect, in seconds",
}
DIRECT_SKIP = {"PYTHONPATH"}  # passed on to child processes, not a setting

# --- the web container (nginx: Dockerfile.web and docker/web) ---------------------------------------------------
WEB = {
    "PORTAL_API_UPSTREAM": "The portal API the web app's nginx forwards `/api/` to",
    "MAX_UPLOAD_SIZE": "Largest upload the web app's nginx accepts (on a cluster, from `deploy.env`)",
    "TRUSTED_PROXIES": "Addresses or CIDR ranges of proxies in front of the web app. It then passes on the caller's "
                       "address from their `X-Forwarded-For`, not the proxy's own. Empty: the web app's peer is the "
                       "caller. `WEB_TRUSTED_PROXIES` in the compose file and `deploy.env`",
    "TUNNEL_PROXIES": "cloudflared's address range: Cloudflare's caller header is passed to the portal only from "
                      "there, so nobody reaching the web app another way can name themselves (#336)",
    "GRAFANA_UPSTREAM": "Grafana, served at `/grafana/` to super admins (#397). Empty: no Grafana. Set by the "
                        "Kubernetes deploy from `MONITORING`",
}

# --- read by scripts/deploy.sh with a default, but not in deploy.env.example: set them in deploy.env to change ----
DEPLOY_EXTRA = {
    "MUSIC_TAG": "The music reader's image tag; from `.last-build` unless set",
    "FORMULA_TAG": "The formula reader's image tag; from `.last-build` unless set",
    "WORKER_GRACE_SECONDS": "How long an engine worker being stopped may finish its documents, in seconds",
    "CLAMAV_IMAGE": "ClamAV's image, pinned by digest in `deploy.sh`",
    "CLAMAV_MEMORY_REQUEST": "Memory reserved for ClamAV",
    "CLAMAV_MEMORY_LIMIT": "ClamAV's memory limit",
    "EGRESS_PROXY_IMAGE": "The egress proxy's image (Squid), pinned by digest in `deploy.sh`",
    "PROMETHEUS_RETENTION_SIZE": "The most history Prometheus keeps by size (for example `17GB`); empty: 85% of "
                                 "`PROMETHEUS_SIZE`",
    "PROMETHEUS_IMAGE": "Prometheus's image, pinned by digest in `deploy.sh`",
    "GRAFANA_IMAGE": "Grafana's image, pinned by digest in `deploy.sh`",
    "KSM_IMAGE": "kube-state-metrics's image, pinned by digest in `deploy.sh`",
    "REDIS_EXPORTER_IMAGE": "The Redis exporter's image, pinned by digest in `deploy.sh`",
    "POSTGRES_EXPORTER_IMAGE": "The Postgres exporter's image, pinned by digest in `deploy.sh`",
}
DEPLOY_LINKS = [  # (names or prefixes, link): the first that matches
    (["KUBECONFIG_PATH", "KUBE_CONTEXT", "NAMESPACE"], "../deployment/first-cluster/README.md"),
    (["FRONTEND", "API_ORIGIN_", "WORKER_NAME"], "../deployment/cloudflare.md#the-web-app-on-a-cloudflare-worker"),
    (["TRUSTED_PROXY_CIDRS", "WEB_TRUSTED_PROXIES"], "../../deploy/k8s/README.md#network-policies-and-the-redis-password"),
    (["GATEWAY_"], "ai-models.md#the-gateway"),
    (["AUTOSCALE", "ENGINE_WORKERS_MAX", "VERAPDF_MAX", "LITELLM_MAX", "SCALE_DOWN_SECONDS", "WORKER_GRACE_SECONDS"],
     "../../deploy/k8s/README.md#scaling-with-the-queue"),
    (["VERAPDF_"], "configuration.md#verapdf-service"),
    (["ENGINE_WORKERS", "WORKER_CONCURRENCY", "WORKER_MEMORY_LIMIT"], "../../deploy/k8s/README.md#sizing"),
    (["ADOBE_WORKERS"], "adobe-autotag.md"),
    (["MALWARE_SCAN", "CLAMAV_"], "malware-scanning.md"),
    (["MUSIC_"], "../../deploy/k8s/README.md#music-notation-read-note-by-note"),
    (["FORMULA_"], "../../deploy/k8s/README.md#formulas-read-a-second-time-from-their-pictures"),
    (["INGRESS_", "CERT_ISSUER"], "../deployment/first-cluster/middleware.md"),
    (["EGRESS_DENY", "NODE_CIDRS", "KUBE_API_"], "../../deploy/k8s/README.md#outbound-traffic-default-deny"),
    (["EGRESS_", "CLUSTER_CIDRS"], "../../deploy/k8s/README.md#outbound-traffic-the-egress-proxy"),
    (["ENGINE_NODES"], "../deployment/first-cluster/scaling.md#4-the-pdf-engine-on-its-own-nodes"),
    (["NODE_ENCRYPTION"], "../../deploy/k8s/README.md#deployenv-at-a-glance"),
    (["BACKUP"], "backups-and-migrations.md#nightly-backups-on-the-cluster"),
    (["DB_SIZE", "REDIS_SIZE", "FILES_SIZE", "JOBDATA_SIZE", "PROMETHEUS_SIZE", "GRAFANA_SIZE"],
     "../deployment/first-cluster/storage.md#growing-a-volume"),
    (["MONITORING", "PROMETHEUS_", "GRAFANA_", "KSM_", "REDIS_EXPORTER_", "POSTGRES_EXPORTER_", "VOLUME_ALERT_"],
     "monitoring.md#prometheus-and-grafana"),
    (["STORAGE_CLASS", "SHARED_STORAGE_CLASS"],
     "../deployment/first-cluster/storage.md"),
    (["CLOUDFLARED_"], "../deployment/cloudflare.md#a-tunnel-in-front-tested"),
]

# --- the local Compose stacks' own (${NAME} in the compose files, beyond the settings above) --------------------
COMPOSE_ONLY = {
    "API_PORT": ("`8000`", "The port the engine's API is published on, on this machine"),
    "LITELLM_PORT": ("`4000`", "The port the AI gateway is published on, on this machine"),
    "PORTAL_WEB_PORT": ("`8088`", "The port the web app is published on, on this machine"),
    "FAKE_ADOBE_PORT": ("`8090`", "The port the fake Adobe is published on (`--profile fake-adobe`)"),
    "WORKER_CONCURRENCY": ("`2`", "Documents an engine worker processes at once"),
    "ADOBE_WORKER_CONCURRENCY": ("`2`", "Documents the Adobe worker handles at once"),
    "WEB_TRUSTED_PROXIES": ("empty", "Passed to the web container as `TRUSTED_PROXIES`"),
}

# --- the operator's scripts: read from the shell environment -----------------------------------------------------
SCRIPT_VARS = {  # name: (read by, default, what it does)
    "KUBECONFIG": ("deploy.sh, backup.sh, restore.sh, rotate-secret.sh", "`$HOME/.kube/config`",
                   "The kubeconfig, when `deploy.env` has no `KUBECONFIG_PATH`"),
    "GHCR_TOKEN": ("build.sh", "`gh auth token`", "A registry token with `write:packages`, to push the images"),
    "GHCR_PULL_TOKEN": ("deploy.sh", "`gh auth token`", "The token the cluster pulls the images with"),
    "TAG": ("build.sh", "the commit", "The images' tag"),
    "TRIVY_IMAGE": ("build.sh", "pinned by digest in `build.sh`", "The image scanner's image"),
    "BACKUP_DIR": ("backup.sh", "`./backups`", "Where backups go (or `--out`)"),
    "BACKUP_AGE_RECIPIENT": ("backup.sh", "empty", "`--encrypt-config` with age (recommended): the recipient"),
    "BACKUP_AGE_IDENTITY": ("restore.sh", "empty", "The age identity file that decrypts the config archive"),
    "BACKUP_PASSPHRASE": ("backup.sh, restore.sh", WHEN_USED,
                          "`--encrypt-config` with openssl, when no age recipient is set"),
    "K8S_NAMESPACE": ("backup.sh, restore.sh", "empty",
                      "Back up or restore the cluster in this namespace (as `--k8s` does)"),
    "PORTAL_COMPOSE": ("backup.sh, restore.sh", "the portal's Compose stack", "How to run the portal's Compose stack"),
    "ENGINE_COMPOSE": ("backup.sh, restore.sh", "`docker compose`", "How to run the engine's Compose stack"),
    "PORTAL_DB_EXEC": ("backup.sh, restore.sh", "its database container",
                       "How to run `pg_dump` and `psql` in the portal's database"),
    "LITELLM_DB_EXEC": ("backup.sh, restore.sh", "its database container", "The same for the AI gateway's database"),
    "PORTAL_CLI": ("backup.sh, restore.sh", "the portal API container", "How to run the portal CLI with its settings"),
}
# Shell variables the scripts set for themselves or for their tests
SCRIPT_SKIP = {"K8S_NS_FROM_FILE", "RENDER_ALL", "TMPDIR", "ROTATE_K8S_DIR"}


# =================================================================================================================
@dataclass
class Row:
    name: str
    default: str
    description: str
    link: str = ""
    where: str = ""


@dataclass
class Table:
    title: str
    rows: list[Row]
    intro: str = ""
    where_header: str = ""


@dataclass
class Part:
    anchor: str
    title: str
    intro: str
    tables: list[Table] = field(default_factory=list)


def one_line(text: str) -> str:
    return " ".join(text.split())


def is_secret(name: str) -> bool:
    return bool(SECRET_NAME.search(name)) and name not in NOT_SECRET


def generated_names() -> set[str]:
    """The keys scripts/deploy.sh init writes with a new random value."""
    sh = DEPLOY_SH.read_text()
    names = set(re.findall(r'echo "([A-Z][A-Z0-9_]*)=[^"]*\$(?:\(rand|\(jwt_key|master)', sh))
    for loop in re.findall(r"for k in ([A-Z0-9_ ]+); do\n[^\n]*\n[^\n]*\$\(rand", sh):
        names.update(loop.split())
    for a, b in re.findall(r"^\s*shared_key ([A-Z0-9_]+) ([A-Z0-9_]+)", sh, re.M):
        names.update((a, b))
    return names


def show_default(name: str, value, generated: set[str]) -> str:
    if is_secret(name) and isinstance(value, str):
        return GENERATED if name in generated else WHEN_USED
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, Path) and not value.is_absolute():
        value = f"./{value}"
    value = str(value)
    if value == "":
        return "empty"
    value = re.sub(r"://([^:/@]+):[^@/]+@", r"://\1:…@", value)  # no password from a URL, even a local default's
    if "@sha256:" in value:
        return f"`{value.split('@')[0]}` (pinned by digest)"
    return f"`{value}`"


def in_group(name: str, members: list[str]) -> bool:
    return any(name == m or (m.endswith("_") and name.startswith(m)) for m in members)


def model_tables(model, prefix: str, groups, links: dict, generated: set[str], missing: list[str]) -> list[Table]:
    rows: dict[str, list[Row]] = {g[0]: [] for g in groups}
    for name, info in model.model_fields.items():
        env = (prefix + name).upper()
        desc = one_line(info.description or "")
        if not desc:
            missing.append(f"{env} ({model.__module__}): no docstring under the field")
        title, link = next(((t, lk) for t, lk, members in groups if in_group(name, members)), ("Other", ""))
        rows.setdefault(title, []).append(Row(env, show_default(env, info.default, generated), desc,
                                              links.get(name, link)))
    return [Table(t, r) for t, r in rows.items() if r]


def parse_deploy_env(text: str) -> list[tuple[str, str, str]]:
    """deploy.env.example's settings: (name, value, description), in the file's order."""
    lines = text.splitlines()
    out, block, run = [], [], []

    def close():
        bullets: dict[str, str] = {}
        cur: list[str] | None = None
        for c in block:
            m = re.match(r"- ((?:[A-Z][A-Z0-9_]*(?:, | and | or )?)+):\s*(.*)", c)
            if m:
                cur = [m.group(2)]
                for n in re.split(r", | and | or ", m.group(1)):
                    bullets[n] = cur  # shared list: continuation lines reach every name in the bullet
            elif cur is not None and c.startswith("  "):
                cur.append(c.strip())
            else:
                cur = None
        for name, value in run:
            if name in bullets:
                desc = " ".join(bullets[name])
            elif len(run) == 1:
                desc = " ".join(block)
            else:
                desc = ""
            out.append((name, value, one_line(desc)))

    for line in lines + [""]:
        key = re.match(r"([A-Z][A-Z0-9_]*)=(.*)$", line)
        if key:
            run.append((key.group(1), key.group(2).strip().strip('"').strip("'")))
            continue
        if run:
            close()
            block, run = [], []
        if line.startswith("#"):
            block.append(line[1:].removeprefix(" "))
        else:
            block = []
    return out


def link_for(name: str, table) -> str:
    return next((lk for members, lk in table if in_group(name, members) or name.startswith(tuple(members))), "")


def env_reads(paths: list[Path], pattern: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for p in paths:
        for f in ([p] if p.is_file() else sorted(p.rglob("*.py"))):
            if "tests" in f.parts:
                continue
            for n in re.findall(pattern, f.read_text(errors="ignore")):
                found.setdefault(n, set()).add(str(f.relative_to(ROOT)))
    return found


def build() -> tuple[str, list[str]]:
    """The page, and what's missing a description (empty when every setting has one)."""
    sys.path.insert(0, str(ROOT))
    from app.config import Settings
    from portal.config import PortalSettings

    missing: list[str] = []
    generated = generated_names()
    parts: list[Part] = []

    # The engine and the portal
    parts.append(Part("the-engine", "The engine",
                      "Read by the engine's API and workers (`app/config.py`): locally from `.env`, on a cluster from "
                      "`deploy/k8s/engine.env`. Each name is the setting's field name in capitals.",
                      model_tables(Settings, "", ENGINE_GROUPS, ENGINE_LINKS, generated, missing)))

    # The AI gateway
    reads = set(re.findall(r"os\.environ/([A-Z][A-Z0-9_]*)", LITELLM.read_text()))
    for n in sorted(reads - GATEWAY.keys()):
        missing.append(f"{n} (config/litellm.yaml): add it to GATEWAY in scripts/settings_reference.py")
    rows = [Row(n, d if not is_secret(n) else (GENERATED if n in generated else d), desc,
                GATEWAY_LINKS.get(n, "ai-models.md#the-gateway")) for n, (d, desc) in GATEWAY.items()]
    parts.append(Part("the-ai-gateway", "The AI gateway",
                      "Read by the AI gateway (LiteLLM): its models are in `config/litellm.yaml`, which names these. "
                      "Locally from `.env`; on a cluster from `deploy/k8s/engine.env`, from which `deploy.sh` gives "
                      "the gateway only its own.", [Table("", rows)]))

    parts.append(Part("the-portal", "The portal",
                      "Read by the portal's API and worker (`portal/config.py`): locally from `.env.portal`, on a "
                      "cluster from `deploy/k8s/portal.env`. Each name is `PORTAL_` and the field name in capitals.",
                      model_tables(PortalSettings, "PORTAL_", PORTAL_GROUPS, PORTAL_LINKS, generated, missing)))

    # Passwords the manifests and Compose files use
    settings_envs = {n.upper() for n in Settings.model_fields} | {"PORTAL_" + n.upper()
                                                                for n in PortalSettings.model_fields}
    compose_reads = env_reads(COMPOSE, r"\$\{([A-Z][A-Z0-9_]*)")
    for n in sorted((generated | {c for c in compose_reads if is_secret(c)})
                    - settings_envs - GATEWAY.keys() - SECRETS.keys()):
        missing.append(f"{n} (made by deploy.sh init, or a Compose file's secret): add it to SECRETS")
    rows = [Row(n, GENERATED if n in generated else "required", desc, SECRET_LINKS.get(n, SECRETS_DEFAULT_LINK),
                where) for n, (where, desc) in SECRETS.items()]
    parts.append(Part("passwords-and-keys", "Passwords and keys",
                      "The services' settings above that are secrets say so in their default. These others are used "
                      "by the manifests and the Compose files rather than read as settings. On a cluster "
                      "`scripts/deploy.sh init` makes each one (\"generated\"); locally you set them in `.env` or "
                      "`.env.portal`. None is ever shown here.", [Table("", rows, where_header="In")]))

    # Read directly by the code
    found = env_reads([ROOT / d for d in DIRECT_CODE],
                      r"(?:environ\.get|getenv|\be\.get)\(\s*\"([A-Z][A-Z0-9_]*)\"")
    defaults = {}
    for d in DIRECT_CODE:
        for f in sorted((ROOT / d).rglob("*.py")):
            for n, v in re.findall(r"(?:environ\.get|getenv)\(\s*\"([A-Z][A-Z0-9_]*)\",\s*\"([^\"]*)\"",
                                   f.read_text(errors="ignore")):
                defaults.setdefault(n, v)
    for n in sorted(found.keys() - DIRECT.keys() - DIRECT_SKIP):
        missing.append(f"{n} ({', '.join(sorted(found[n]))}): add it to DIRECT")
    for n in sorted(DIRECT.keys() - found.keys()):
        missing.append(f"{n}: in DIRECT but no code reads it any more; remove it")
    rows = [Row(n, show_default(n, defaults.get(n, ""), generated), DIRECT[n], "", ", ".join(
        f"`{f}`" for f in sorted(found.get(n, ())))) for n in DIRECT]
    parts.append(Part("read-directly-by-the-code", "Read directly by the code",
                      "Variables the code reads from its environment itself, not through the settings above.",
                      [Table("", rows, where_header="Read by")]))

    # The web container
    web_env = next((ln for ln in (ROOT / "Dockerfile.web").read_text().splitlines() if ln.startswith("ENV ")), "")
    web_defaults = dict(re.findall(r"\b([A-Z][A-Z0-9_]*)=(\"[^\"]*\"|\S+)", web_env))
    web_reads = env_reads([f for f in WEB_FILES if f.name != "Dockerfile.web"], r"\$\{([A-Z][A-Z0-9_]*)[}:]")
    for n in web_defaults:
        web_reads.setdefault(n, set()).add("Dockerfile.web")
    for n in sorted(web_reads.keys() - WEB.keys()):
        missing.append(f"{n} ({', '.join(sorted(web_reads[n]))}): add it to WEB")
    rows = [Row(n, show_default(n, web_defaults.get(n, "").strip('"'), generated), desc,
                "configuration.md#the-portal" if "PROXIES" in n else "") for n, desc in WEB.items()]
    parts.append(Part("the-web-container", "The web container",
                      "Read by the web app's nginx container (`Dockerfile.web`, `docker/web`). The compose file and "
                      "the manifests set them.", [Table("", rows)]))

    # deploy.env
    env_rows = []
    example = parse_deploy_env(DEPLOY_ENV.read_text())
    for n, value, desc in example:
        if not desc:
            missing.append(f"{n} (deploy/k8s/deploy.env.example): no comment above it, or no \"# - {n}: ...\" "
                           "bullet in the comment over its lines")
        env_rows.append(Row(n, show_default(n, value, generated), desc, link_for(n, DEPLOY_LINKS)))
    sh = DEPLOY_SH.read_text()
    sh_defaults = dict(re.findall(r"\$\{([A-Z][A-Z0-9_]*):-([^}$]*)\}", sh))
    in_example = {n for n, _, _ in example}
    deploy_reads = set(sh_defaults) | set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):-", sh))
    for n in sorted(deploy_reads - in_example - DEPLOY_EXTRA.keys() - SCRIPT_VARS.keys() - SCRIPT_SKIP):
        missing.append(f"{n} (scripts/deploy.sh): add it to deploy.env.example, or to DEPLOY_EXTRA")
    extra_rows = [Row(n, show_default(n, sh_defaults.get(n, ""), generated), desc, link_for(n, DEPLOY_LINKS))
                  for n, desc in DEPLOY_EXTRA.items()]
    parts.append(Part("on-a-cluster-deployenv", "On a cluster: `deploy.env`",
                      "Read by `scripts/deploy.sh` (and `build.sh`, `backup.sh`) from `deploy/k8s/deploy.env`, made "
                      "from `deploy/k8s/deploy.env.example` by `scripts/deploy.sh init`. No secrets: those are in "
                      "`engine.env` and `portal.env`. Where a setting here overlaps an engine or portal one, "
                      "`deploy.sh` sets that from it.",
                      [Table("In `deploy.env.example`", env_rows, "The default is the example's value; the ones "
                             "naming a host or registry are placeholders to change."),
                       Table("Not in the example", extra_rows, "`deploy.sh` has a default for these; set one in "
                             "`deploy.env` to change it.")]))

    # The Compose stacks and the scripts: what isn't already above
    documented = settings_envs | GATEWAY.keys() | SECRETS.keys() | DIRECT.keys() | WEB.keys() | in_example | \
        DEPLOY_EXTRA.keys()
    for n in sorted(compose_reads.keys() - documented - COMPOSE_ONLY.keys()):
        missing.append(f"{n} ({', '.join(sorted(compose_reads[n]))}): add it to COMPOSE_ONLY")
    rows = [Row(n, d, desc) for n, (d, desc) in COMPOSE_ONLY.items()]
    parts.append(Part("the-local-compose-stacks", "The local Compose stacks",
                      "Read by `docker-compose.yml` and `docker-compose.portal.yml` from `.env` and `.env.portal`, "
                      "beyond the settings above.", [Table("", rows)]))

    script_reads = env_reads([ROOT / "scripts" / s for s in SCRIPTS], r"\$\{([A-Z][A-Z0-9_]*):[-=]")
    for n in sorted(script_reads.keys() - documented - SCRIPT_VARS.keys() - SCRIPT_SKIP):
        missing.append(f"{n} ({', '.join(sorted(script_reads[n]))}): add it to SCRIPT_VARS")
    rows = [Row(n, d, desc, "", where) for n, (where, d, desc) in SCRIPT_VARS.items()]
    parts.append(Part("the-scripts", "The scripts",
                      "Read from the shell environment by the operator's scripts in `scripts/`, not by the services. "
                      "Each script's header says more.", [Table("", rows, where_header="Read by")]))

    return render(parts), missing


def cell(text: str) -> str:
    """A description as a table cell: no closing full stop, as configuration.md writes them."""
    return text.rstrip(" .;").replace("|", "\\|")


def render(parts: list[Part]) -> str:
    out = [
        "# Settings reference", "",
        "[Operations](README.md) › Settings reference", "",
        "Every setting the service reads from its environment, with its default and what it does. "
        "[Configuration](configuration.md) is the overview: where settings live and the ones most often changed. "
        "What super admins change in the app (branding, limits, retention, services, AI levels) is in the "
        "[super admin guide](../user-guide/super-admins/settings.md).", "",
        "> This page is generated by `scripts/settings_reference.py` from the code: don't edit it by hand. A setting's "
        "description is kept where the setting is defined (the docstring under it in `app/config.py` or "
        "`portal/config.py`, its comment in `deploy/k8s/deploy.env.example`, or the script's tables for the rest). "
        "Change it there and run `python scripts/settings_reference.py`; a test fails when the two differ.", "",
        "Secrets never show a value: their default says *generated* (`scripts/deploy.sh init` makes one) or "
        "*set when used*.", "",
        "## Contents", "",
    ]
    out += [f"{i}. [{p.title}](#{p.anchor})" for i, p in enumerate(parts, 1)]
    for p in parts:
        out += ["", f"## {p.title}", "", p.intro]
        for t in p.tables:
            if t.title:
                out += ["", f"### {t.title}"]
            if t.intro:
                out += ["", t.intro]
            has_link = any(r.link for r in t.rows)
            head = ["Setting", "Default"] + ([t.where_header] if t.where_header else []) + ["What it does"] + (
                ["More"] if has_link else [])
            out += ["", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            for r in t.rows:
                cells = [f"`{r.name}`", r.default] + ([r.where] if t.where_header else []) + [cell(r.description)]
                if has_link:
                    cells.append(f"[more]({r.link})" if r.link else "")
                out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    s = heading.strip().lower().replace("`", "")
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def broken_links(page: str) -> list[str]:
    """Links on the page whose file, or heading, isn't there."""
    bad = []
    for target in sorted(set(re.findall(r"\]\(([^)#]*)(?:#([^)]*))?\)", page))):
        path, anchor = target
        f = (OUT.parent / path).resolve() if path else OUT
        text = page if not path else (f.read_text() if f.is_file() else None)
        if text is None:
            bad.append(f"{path}: no such file")
            continue
        if anchor and anchor not in {slug(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}:
            bad.append(f"{path or OUT.name}#{anchor}: no such heading")
    return bad


def main(argv: list[str]) -> int:
    page, missing = build()
    problems = missing + broken_links(page)
    if problems:
        print("Settings without a description, or links that don't lead anywhere:", file=sys.stderr)
        for m in problems:
            print(f"  {m}", file=sys.stderr)
        return 1
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text() != page:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python scripts/settings_reference.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(page)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
