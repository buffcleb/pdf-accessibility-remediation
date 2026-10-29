"""Deployment settings for the portal API, from environment variables (prefix PORTAL_).

These are things that differ per deployment (URLs, secrets, keys). Settings an admin changes at
runtime (messages, limits, retention) live in the database; see settings_store.py.

The string under each setting is its description: it's what docs/operations/settings-reference.md shows
(scripts/settings_reference.py writes it, and a test fails when a setting has none), so write it for the person
deploying the service.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PortalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTAL_", env_file=".env", extra="ignore",
                                      use_attribute_docstrings=True)

    database_url: str = "postgresql+psycopg://portal:portal@localhost:5432/portal"
    """The portal's PostgreSQL. On Kubernetes the manifests set it: the portal connects as its own rows-only account,
    `portal_app`, and migrations run as the owner."""
    redis_url: str = "redis://localhost:6379/1"
    """Redis for the rate limits. On Kubernetes it's the portal's own, `redis-portal`, apart from the engine's queue
    (#472)."""

    cors_origins: str = ""
    """Browser-facing origins allowed to call the API (the web app), comma-separated."""
    app_url: str = "http://localhost:5173"
    """The address people use to reach the web app, used in emails."""

    jwt_private_keys: str = ""
    """Sign-in token signing keys: ES256 private keys in PEM. The first signs; all are accepted, so keys can be
    rotated by putting a new one first. Separate keys with a blank line. Generate with
    `python -m portal.cli genkeys`."""
    jwt_private_keys_file: str = ""
    """A file holding the signing keys, instead of `PORTAL_JWT_PRIVATE_KEYS`."""
    jwt_issuer: str = "pdf-remediation-portal"
    """The issuer written into sign-in tokens and required of them."""
    access_token_minutes: int = 10
    """How long a sign-in (access) token lasts, in minutes; the refresh cookie gets a new one."""

    cookie_secure: bool = True
    """The refresh cookie is sent over HTTPS only. `false` only for a local stack on plain HTTP."""
    cookie_domain: str = ""
    """The refresh cookie's domain; empty gives a host-only cookie."""
    refresh_cookie_name: str = "portal_refresh"
    """The refresh cookie's name."""

    secret_key: str = Field(default="", min_length=0)
    """HMAC key for one-time codes and refresh-token hashes (32 or more random bytes, base64 or hex). Generate with
    `python -m portal.cli genkeys`."""

    bootstrap_super_admins: str = ""
    """Email addresses that become super admins at their first sign-in, comma-separated."""

    # Mail
    mail_backend: str = "smtp"
    """How email is sent: `smtp`, `ses`, `plunk`, `cloudflare`, `console` (logged only) or `memory` (tests)."""
    mail_from: str = "PDF Remediation <no-reply@localhost>"
    """The sender. The name can be overridden in branding; the address must be on a domain the provider has
    verified."""
    mail_reply_to: str = ""
    """A Reply-To address for every email; empty sends none."""
    mail_attempts: int = 3
    """Tries for temporary failures (timeouts, throttling, server errors)."""
    smtp_host: str = "localhost"
    """SMTP relay host."""
    smtp_port: int = 1025
    """SMTP relay port (1025 is the local stack's Mailpit)."""
    smtp_username: str = ""
    """SMTP user name; empty: no sign-in."""
    smtp_password: str = ""
    """SMTP password."""
    smtp_starttls: bool = False
    """Upgrade the SMTP connection with STARTTLS."""
    smtp_ssl: bool = False
    """Connect to SMTP over TLS from the start (port 465)."""
    ses_region: str = ""
    """Amazon SES region; empty: the AWS default region."""
    ses_configuration_set: str = ""
    """An Amazon SES configuration set to send with; empty: none."""
    plunk_api_key: str = ""
    """Plunk secret key (`sk_...`)."""
    plunk_base_url: str = "https://next-api.useplunk.com"
    """Plunk's API."""
    cloudflare_account_id: str = ""
    """Cloudflare account, for Cloudflare's Email Service."""
    cloudflare_api_token: str = ""
    """A Cloudflare token allowed to send email."""

    # One-time codes
    otp_minutes: int = 10
    """How long a sign-in code lasts, in minutes."""
    otp_max_attempts: int = 5
    """Wrong tries allowed on one sign-in code before it stops working."""
    # Rate limits
    rate_window_seconds: int = 900
    """The window, in seconds, for the sign-in rate limits below."""
    rate_otp_per_email: int = 5
    """Sign-in codes sent to one address per window."""
    rate_otp_per_ip: int = 30
    """Sign-in codes requested from one IP address per window."""
    rate_verify_per_ip: int = 50
    """Sign-in code checks from one IP address per window."""
    rate_downloads_per_hour: int = 600
    """Files one person may download per hour (#357; 0 turns it off). Past it, a plain 429."""
    rate_exports_per_hour: int = 120
    """Report and person-data exports one person may make per hour (0 turns it off)."""
    rate_uploads_per_hour: int = 200
    """Upload requests one person may make per hour (one can hold several files; 0 turns it off)."""
    rate_document_exports_per_hour: int = 20
    """Zips of many documents' files one person may make per hour (#481): one each, however many files (0 turns it
    off)."""
    turnstile_site_key: str = ""
    """Cloudflare Turnstile's site key, for a bot check on "Email me a code". Off unless both keys are set."""
    turnstile_secret_key: str = ""
    """Cloudflare Turnstile's secret key."""
    turnstile_fail_open: bool = True
    """When Cloudflare can't be reached, sign-in is allowed (the rate limits still apply) rather than refused."""

    backup_expected: bool = False
    """Alert super admins when no nightly backup has finished for a while (#333). Set by the Kubernetes deploy from
    `BACKUPS=cronjob`; off where backups are taken another way (the hosts' own, or none)."""
    siem_mode: str = "off"
    """The audit log to the campus SIEM (#456): `off`, `syslog` (RFC 5424 over TCP, with TLS unless
    `PORTAL_SIEM_SYSLOG_TLS` is off) or `https` (JSON lines, or Splunk's HTTP Event Collector format). The portal
    worker sends every event in order, from the first, and keeps its place."""
    siem_syslog_host: str = ""
    """The syslog collector's host."""
    siem_syslog_port: int = 6514
    """The syslog collector's port."""
    siem_syslog_tls: bool = True
    """Syslog over TLS."""
    siem_syslog_ca: str = ""
    """The syslog collector's CA (PEM, `\\n` for line breaks), when the system's CAs don't cover it."""
    siem_https_url: str = ""
    """The HTTPS collector's address."""
    siem_https_token: str = ""
    """The HTTPS collector's token."""
    siem_https_format: str = "lines"
    """`lines` (application/x-ndjson, a Bearer token) or `splunk` (HTTP Event Collector, a "Splunk" token)."""
    siem_batch: int = 500
    """Audit events sent to the SIEM at a time."""
    siem_pull_token: str = ""
    """For a SIEM that polls: `GET /api/v1/admin/audit/export` with this as a Bearer token. Empty: no pull API."""
    pdfcheck_isolated: bool = True
    """An upload's pages are counted in a child process with the limits below (#356); past them the upload is
    refused as too large or complex to open safely. Off only in tests."""
    pdfcheck_memory_mb: int = 1024
    """Memory the page-counting child process may use, in MB."""
    pdfcheck_cpu_s: int = 30
    """CPU time the page-counting child process may use, in seconds."""
    pdfcheck_timeout_s: float = 60
    """How long the page-counting child process may take in all, in seconds."""
    trust_proxy_headers: bool = False
    """Trust the client-address header from a proxy in front of the API (Cloudflare, a load balancer, nginx)."""
    trusted_proxies: str = ""
    """With trusted proxy headers: the addresses (CIDRs, comma-separated) of the proxies in front of the API, such as
    cloudflared, the web pod and an ingress controller. The header counts only on a connection from one of them;
    anyone else is known by their own address, so they can't pick a fresh rate-limit bucket by setting a header
    (#336). Empty: every connection's header counts (a local stack behind its own proxy). The Kubernetes deploy sets
    it from `TRUSTED_PROXY_CIDRS`."""
    client_ip_header: str = "cf-connecting-ip"
    """With trusted proxy headers: the header holding the caller's address. `cf-connecting-ip` behind Cloudflare;
    `x-client-ip` behind the Cloudflare Worker front end; `x-forwarded-for` behind any other proxy."""
    trusted_proxy_hops: int = 1
    """With `x-forwarded-for`: the caller's address is the one this many places from the end (each trusted proxy
    adds one; the web app's nginx adds one), never from the front, which a caller can make up."""

    # Object storage (any S3 API: AWS S3, SeaweedFS, MinIO...)
    storage_backend: str = "s3"
    """`s3`, or `memory` for tests."""
    s3_endpoint_url: str = ""
    """The object store's address; empty for AWS S3."""
    s3_region: str = "us-east-1"
    """The object store's region."""
    s3_access_key: str = ""
    """The object store's access key."""
    s3_secret_key: str = ""
    """The object store's secret key."""
    s3_bucket: str = "pdf-remediation"
    """The bucket documents are stored in."""
    s3_create_bucket: bool = False
    """Create the bucket at start (demo stores only)."""
    s3_sse: str = "AES256"
    """The server-side encryption header sent with each object; empty leaves it out (some S3 clones reject it)."""

    # Remediation engine (internal network only)
    engine_url: str = "http://localhost:8000"
    """The engine's API. Set by the compose file or manifests."""
    engine_api_key: str = ""
    """The key the portal sends the engine as `X-API-Key`: the engine's `SERVICE_API_KEY`. `deploy.sh init` makes
    it, in both files."""
    engine_ai_provider: str = "localai"
    """The fallback AI provider for Describe with AI when no model is given. Documents are never sent to it: one
    without an AI level is made with no AI (#659)."""
    metrics_enabled: bool = True
    """`/metrics` for the deployment's Prometheus (#397)."""
    worker_metrics_port: int = 9464
    """The port of the portal worker's own metrics (it serves no requests)."""
    grafana_url: str = ""
    """Grafana's address in the web app (for example `/grafana/`). Empty: not part of this deployment. Set by the
    Kubernetes deploy from `MONITORING`."""
    prometheus_url: str = ""
    """Prometheus inside the cluster, for Administration › System and its alerts. Empty: not part of this
    deployment. Set by the Kubernetes deploy from `MONITORING`."""
    malware_scan: str = "off"
    """Uploads and help-desk attachments scanned before they're stored (#455): `clamav`, or `off` (nothing is
    scanned). On a cluster, set by `MALWARE_SCAN` in `deploy.env`."""
    clamd_host: str = "clamav"
    """ClamAV's host."""
    clamd_port: int = 3310
    """ClamAV's port."""
    clamd_timeout_seconds: float = 120.0
    """How long a scan may take, in seconds. A whole upload is streamed, so it allows a large one."""
    malware_signatures_days: int = 3
    """Older malware signatures are an alert: freshclam isn't getting updates."""
    engine_ai_model: str = ""
    """The fallback AI's model; empty: the provider's default."""
    engine_max_in_flight: int = 4
    """Documents kept at the engine at once, at least. When the engine's running workers have more places
    (`GET /api/v1/capacity`), the portal uses that number."""
    engine_in_flight_ceiling: int = 64
    """Never more documents than this at the engine, however many workers answer."""
    engine_capacity_cache_seconds: float = 30.0
    """How often the portal asks the engine how many places its workers have, in seconds."""
    engine_timeout_seconds: float = 60.0
    """How long a call to the engine may take, in seconds."""
    gateway_admin_url: str = ""
    """Where super admins open the AI gateway's (LiteLLM's) admin UI, linked from Administration. Empty (the default)
    shows how to reach it instead of a link: set it only to an address super admins' browsers can reach."""

    # Background worker
    worker_poll_seconds: float = 3.0
    """How often the portal worker looks for work (documents to send, results, email), in seconds."""
    worker_sweep_minutes: int = 60
    """How often the portal worker's housekeeping runs (what retention says has expired, help-desk reminders,
    expired API tokens), in minutes."""
    review_findings_max: int = 300
    """Review items (what to check, most serious first) kept with a document and with each saved version, to list in
    the app; the rest stay in the JSON report. Raise it with the engine's `VERIFY_AI_MAX_ITEMS`, or part of what was
    reviewed has nowhere to show."""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def bootstrap_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.bootstrap_super_admins.split(",") if e.strip()}


@lru_cache
def get_portal_settings() -> PortalSettings:
    return PortalSettings()
