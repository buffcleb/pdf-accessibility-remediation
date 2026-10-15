"""Cloudflare Turnstile: a bot check on "Email me a code", so scripts can't use it to send mail to any address.

Off unless PORTAL_TURNSTILE_SECRET_KEY (and the site key, for the page) is set. The page sends the widget's token
with the code request and this checks it with Cloudflare's siteverify API. If Cloudflare can't be reached the check
fails open by default (the rate limits still apply), so an outage there doesn't stop everyone signing in; set
PORTAL_TURNSTILE_FAIL_OPEN=false to refuse instead. https://developers.cloudflare.com/turnstile/
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from portal.config import get_portal_settings

log = logging.getLogger(__name__)

SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


@dataclass
class BotCheck:
    passed: bool
    outcome: str  # off | passed | failed | missing | unavailable
    detail: str = ""


def enabled() -> bool:
    s = get_portal_settings()
    return bool(s.turnstile_secret_key and s.turnstile_site_key)


def check(token: str | None, ip: str | None) -> BotCheck:
    s = get_portal_settings()
    if not enabled():
        return BotCheck(True, "off")
    if not token:
        return BotCheck(False, "missing")
    try:
        r = httpx.post(SITEVERIFY, timeout=8, data={"secret": s.turnstile_secret_key, "response": token,
                                                     **({"remoteip": ip} if ip and ip != "unknown" else {})})
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Turnstile siteverify unreachable: %s", e)
        return BotCheck(s.turnstile_fail_open, "unavailable", str(e)[:200])
    if r.status_code >= 500:
        return BotCheck(s.turnstile_fail_open, "unavailable", f"HTTP {r.status_code}")
    if body.get("success"):
        return BotCheck(True, "passed")
    return BotCheck(False, "failed", ", ".join(body.get("error-codes") or [])[:200])
