"""The deployment's Prometheus, as the portal reads it (#397): the alerts its rules have raised, and fixed queries for
the admin area's System page. PORTAL_PROMETHEUS_URL names it (set by the cluster's MONITORING=on); without it the
portal asks nothing.

Prometheus has no Alertmanager here: the portal's worker reads the firing alerts once a minute and treats each one
like its own alerts (portal/alerts.py), so super admins get them by email and on Attention, with the rest.
"""
from __future__ import annotations

import httpx

from portal.config import get_portal_settings


class Unreachable(Exception):
    """Prometheus didn't answer, or answered with an error."""


class Prometheus:
    def __init__(self, url: str, timeout: float = 10) -> None:
        self.client = httpx.Client(base_url=url.rstrip("/"), timeout=timeout)

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = self.client.get(path, params=params)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise Unreachable(str(e)) from e
        if body.get("status") != "success":
            raise Unreachable(body.get("error") or "Prometheus answered with an error")
        return body.get("data") or {}

    def firing(self) -> list[dict]:
        """The alerts firing now (past their rule's `for:`), each with its labels and annotations."""
        return [a for a in self._get("/api/v1/alerts").get("alerts", []) if a.get("state") == "firing"]

    def query(self, promql: str) -> list[dict]:
        """An instant query's series: [{"metric": {...}, "value": [t, "v"]}]."""
        return self._get("/api/v1/query", {"query": promql}).get("result", [])

    def query_range(self, promql: str, start: float, end: float, step: float) -> list[dict]:
        """A range query's series: [{"metric": {...}, "values": [[t, "v"], ...]}]."""
        return self._get("/api/v1/query_range", {"query": promql, "start": start, "end": end,
                                                 "step": step}).get("result", [])


def get_prometheus() -> Prometheus | None:
    url = get_portal_settings().prometheus_url
    return Prometheus(url) if url else None


def grouped(alerts: list[dict]) -> dict[str, list[dict]]:
    """Firing alerts by name: a rule can fire for several pods at once, and is one problem to the people told."""
    out: dict[str, list[dict]] = {}
    for a in alerts:
        name = (a.get("labels") or {}).get("alertname")
        if name:
            out.setdefault(name, []).append(a)
    return out
