"""The hosts the portal, the AI gateway and the Adobe worker may reach on the internet (#453), worked out from what this
deployment uses, and the egress proxy's configuration that lets them through. Run by scripts/deploy.sh:

    python3 egress_hosts.py hosts  config/litellm.yaml engine.env portal.env    one host:port per line
    python3 egress_hosts.py squid  config/litellm.yaml engine.env portal.env    the proxy's squid.conf

Where each host comes from:
- the gateway's models (config/litellm.yaml): openrouter.ai for OpenRouter models and the Jev route; Bedrock's
  endpoint in AWS_REGION_NAME (engine.env) for Bedrock models; the address in any api_base, read from engine.env
  when it names a variable (the Local AI server);
- the Adobe worker, when Adobe is on (ADOBE_ENABLED=true or ADOBE_WORKERS above 0): ADOBE_BASE_URL's host, and the
  storage host its pre-signed upload and download addresses point at;
- the portal (portal.env): its mail service's API (Cloudflare, Plunk or Amazon SES; plain SMTP doesn't go through the
  proxy), Cloudflare's bot check when it's set up, and the SIEM's address with PORTAL_SIEM_MODE=https (#456; syslog
  doesn't go through the proxy either);
- ClamAV's freshclam, with MALWARE_SCAN=clamav (#455): database.clamav.net, for its signatures;
- EGRESS_ALLOW (deploy.env): any others, comma-separated host or host:port.

EGRESS_PROXY (deploy.env) is log or enforce: log lets everything through and records each destination; enforce lets
through only these hosts. Standard library only.
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlsplit

# The storage behind Adobe's pre-signed addresses (Amazon S3, accelerated): confirm in the proxy's log in log mode
ADOBE_STORAGE = "dcplatformstorageservice-prod-us-east-1.s3-accelerate.amazonaws.com:443"


def read_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def host_port(url: str) -> str | None:
    """host:port of a URL (443 or 80 by its scheme), or None when it isn't one."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if not parts.hostname:
        return None
    port = parts.port or (80 if parts.scheme == "http" else 443)
    return f"{parts.hostname.lower()}:{port}"


def hosts(litellm: str, engine: dict[str, str], portal: dict[str, str], extra: str = "") -> list[str]:
    found: set[str] = set()
    config = open(litellm, encoding="utf-8").read()
    models = re.findall(r"^\s*model:\s*(\S+)", config, re.M)
    region = engine.get("AWS_REGION_NAME") or "us-east-1"
    if any(m.startswith("openrouter/") for m in models) or "openrouter.ai" in config:
        found.add("openrouter.ai:443")
    if any(m.startswith("bedrock/") for m in models):
        found.add(f"bedrock-runtime.{region}.amazonaws.com:443")
    if any(m.startswith("bedrock_mantle/") for m in models):
        found.add(f"bedrock-mantle.{region}.api.aws:443")
    for base in re.findall(r"^\s*api_base:\s*(\S+)", config, re.M):
        url = engine.get(base.split("/", 1)[1], "") if base.startswith("os.environ/") else base
        if url and (hp := host_port(url)):
            found.add(hp)
    workers = engine.get("ADOBE_WORKERS") or os.environ.get("ADOBE_WORKERS", "0")
    if engine.get("ADOBE_ENABLED", "").lower() == "true" or (workers.isdigit() and int(workers) > 0):
        found.add(host_port(engine.get("ADOBE_BASE_URL") or "https://pdf-services-ue1.adobe.io") or "")
        found.add(ADOBE_STORAGE)
    if os.environ.get("MALWARE_SCAN") == "clamav":  # freshclam's signature updates (#455)
        found.add("database.clamav.net:443")
    backend = (portal.get("PORTAL_MAIL_BACKEND") or "smtp").lower()
    if backend == "cloudflare":
        found.add("api.cloudflare.com:443")
    elif backend == "plunk":
        found.add(host_port(portal.get("PORTAL_PLUNK_BASE_URL") or "https://next-api.useplunk.com") or "")
    elif backend == "ses":
        ses = portal.get("PORTAL_SES_REGION") or portal.get("AWS_REGION") or portal.get("AWS_DEFAULT_REGION") or region
        found.add(f"email.{ses}.amazonaws.com:443")
    if portal.get("PORTAL_TURNSTILE_SECRET_KEY"):
        found.add("challenges.cloudflare.com:443")
    if (portal.get("PORTAL_SIEM_MODE") or "").lower() == "https" and portal.get("PORTAL_SIEM_HTTPS_URL"):
        found.add(host_port(portal["PORTAL_SIEM_HTTPS_URL"]) or "")
    for item in (x.strip() for x in extra.split(",")):
        if item and (hp := host_port(item)):
            found.add(hp)
    return sorted(h for h in found if h)


def squid(listed: list[str], mode: str) -> str:
    """The proxy's configuration: in enforce mode only the listed hosts, each on its own port; in log mode anything,
    every destination recorded. Never the content: for HTTPS only the host and port are seen."""
    lines = [
        "# Made by scripts/deploy.sh (deploy/k8s/egress_hosts.py): don't edit, change the settings and deploy again",
        "http_port 3128",
        "pinger_enable off",
        "pid_filename none",
        "cache deny all",
        "cache_mem 0 MB",
        "coredump_dir /tmp",
        "logfile_rotate 0",
        "access_log stdio:/dev/stdout",
        "cache_log stdio:/dev/stderr",
        "acl CONNECT method CONNECT",
        "via off",
        "forwarded_for delete",
    ]
    if mode == "log":
        lines += ["http_access allow all"]
    else:
        for n, hp in enumerate(listed):
            host, port = hp.rsplit(":", 1)
            lines += [f"acl host{n} dstdomain {host}", f"acl port{n} port {port}", f"http_access allow host{n} port{n}"]
        lines += ["http_access deny all"]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] not in ("hosts", "squid"):
        print(__doc__, file=sys.stderr)
        return 2
    listed = hosts(argv[2], read_env(argv[3]), read_env(argv[4]), os.environ.get("EGRESS_ALLOW", ""))
    if argv[1] == "hosts":
        print("\n".join(listed))
    else:
        print(squid(listed, os.environ.get("EGRESS_PROXY", "enforce")), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
