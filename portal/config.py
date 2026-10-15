"""Deployment settings for the portal API, from environment variables (prefix PORTAL_).

These are things that differ per deployment (URLs, secrets, keys). Settings an admin changes at
runtime (messages, limits, retention) live in the database; see settings_store.py.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PortalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTAL_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://portal:portal@localhost:5432/portal"
    redis_url: str = "redis://localhost:6379/1"

    # Browser-facing origins allowed to call the API (the SPA). Comma-separated.
    cors_origins: str = ""
    # Public URL of the SPA, used in emails.
    app_url: str = "http://localhost:5173"

    # JWT signing: ES256 private keys in PEM. The first is used to sign; all are accepted for
    # verification, so keys can be rotated by prepending a new one. Separate keys with a blank line
    # or provide a path via jwt_private_keys_file.
    jwt_private_keys: str = ""
    jwt_private_keys_file: str = ""
    jwt_issuer: str = "pdf-remediation-portal"
    access_token_minutes: int = 10

    # Refresh cookie
    cookie_secure: bool = True
    cookie_domain: str = ""  # empty = host-only cookie
    refresh_cookie_name: str = "portal_refresh"

    # HMAC key for one-time codes and refresh-token hashes (32+ random bytes, base64 or hex).
    secret_key: str = Field(default="", min_length=0)

    # Emails that become super admins on first sign-in (comma-separated). Used to bootstrap.
    bootstrap_super_admins: str = ""

    # Mail
    mail_backend: str = "smtp"  # smtp | ses | plunk | cloudflare | console | memory (tests)
    mail_from: str = "PDF Remediation <no-reply@localhost>"  # the sender name can be overridden in branding
    mail_reply_to: str = ""
    mail_attempts: int = 3  # tries for temporary failures (timeouts, throttling, server errors)
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_ssl: bool = False
    ses_region: str = ""  # empty: the AWS default region
    ses_configuration_set: str = ""
    plunk_api_key: str = ""  # Plunk secret key (sk_...)
    plunk_base_url: str = "https://next-api.useplunk.com"
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""  # a token allowed to send email

    # One-time codes
    otp_minutes: int = 10
    otp_max_attempts: int = 5
    # Rate limits (per window of rate_window_seconds)
    rate_window_seconds: int = 900
    rate_otp_per_email: int = 5
    rate_otp_per_ip: int = 30
    rate_verify_per_ip: int = 50
    # Cloudflare Turnstile bot check on "Email me a code" (off unless both keys are set; see security/botcheck.py)
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    turnstile_fail_open: bool = True  # Cloudflare unreachable: allow (the rate limits still apply) rather than refuse

    # Trust X-Forwarded-For from a proxy in front of the API (Cloudflare, ALB, nginx)
    trust_proxy_headers: bool = False
    # With trusted proxy headers: the header holding the caller's address. Cloudflare sets cf-connecting-ip; behind
    # the Cloudflare Worker front end (which forwards /api/ to the cluster) the Worker passes it as x-client-ip.
    client_ip_header: str = "cf-connecting-ip"
    # Behind proxies that aren't Cloudflare, set the header to x-forwarded-for: the caller's address is then the one
    # this many places from the end of X-Forwarded-For (each trusted proxy adds one; the web app's nginx sends one)
    trusted_proxy_hops: int = 1

    # Object storage (any S3 API: AWS S3, SeaweedFS, MinIO...). Leave the endpoint empty for AWS.
    storage_backend: str = "s3"  # s3 | memory (tests)
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "pdf-remediation"
    s3_create_bucket: bool = False  # create the bucket on start (demo stores only)
    s3_sse: str = "AES256"  # server-side encryption header; empty to omit (some S3 clones reject it)

    # Remediation engine (internal network only)
    engine_url: str = "http://localhost:8000"
    engine_api_key: str = ""
    engine_ai_provider: str = "localai"
    engine_ai_model: str = ""
    # Jobs the portal keeps at the engine at once: at least this many, and as many as the engine's running workers
    # process at once when that's more (workers scaled with the queue), up to the ceiling
    engine_max_in_flight: int = 4
    engine_in_flight_ceiling: int = 64
    engine_capacity_cache_seconds: float = 30.0
    engine_timeout_seconds: float = 60.0
    # Where super admins open the AI gateway's (LiteLLM's) admin UI, linked from Administration. Empty (the default)
    # shows how to reach it instead of a link: set it only to an address super admins' browsers can reach
    # (docker-compose.portal.yml sets the local stack's http://localhost:4000/ui).
    gateway_admin_url: str = ""

    # Background worker
    worker_poll_seconds: float = 3.0
    worker_sweep_minutes: int = 60
    # Review items (what to check, most serious first) kept with a document and with each saved version, to list in
    # the app. The rest stay in the JSON report. Raise it with the engine's VERIFY_AI_MAX_ITEMS, or part of what
    # was reviewed has nowhere to show.
    review_findings_max: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def bootstrap_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.bootstrap_super_admins.split(",") if e.strip()}


@lru_cache
def get_portal_settings() -> PortalSettings:
    return PortalSettings()
