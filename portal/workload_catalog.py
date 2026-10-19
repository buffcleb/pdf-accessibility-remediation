"""What each of the service's workloads is, for Administration > System's pod details (#419): a description in plain
words, where it comes from (its upstream project, or the file in this repository that builds it), the port worth
reaching with `kubectl port-forward`, and whether its image has a shell for `kubectl exec`.

Every Deployment and StatefulSet in deploy/k8s/manifests has an entry (a test checks). A workload that isn't listed
(one added to the cluster by hand) shows its name and commands only.
"""
from __future__ import annotations

REPO = "https://github.com/buffcleb/pdf-accessibility-remediation"


def _ours(path: str) -> dict:
    return {"label": f"This project: {path}", "url": f"{REPO}/blob/main/{path}"}


def _gh(slug: str, label: str | None = None) -> dict:
    return {"label": label or slug, "url": f"https://github.com/{slug}"}


_ENGINE = _ours("Dockerfile")
_PORTAL = _ours("Dockerfile.portal")

CATALOG: dict[str, dict] = {
    "engine-api": {
        "description": "The engine's HTTP API, inside the cluster only: the portal hands it each document and asks how "
                       "it's going. Without it, nothing new starts; documents in progress carry on.",
        "sources": [_ENGINE, _ours("app/api")], "port": 8000, "shell": True},
    "engine-worker": {
        "description": "Remediates documents: analysis, AI, tagging and the checks, a few documents per pod. Scaled "
                       "with the queue. Without it, documents wait.",
        "sources": [_ENGINE, _ours("app/pipeline/orchestrator.py")], "port": None, "shell": True},
    "engine-adobe-worker": {
        "description": "Remediates documents sent to the Adobe Autotag service. Without it, Adobe documents wait; "
                       "the others don't.",
        "sources": [_ENGINE, _ours("app/pipeline/adobe_job.py")], "port": None, "shell": True},
    "engine-beat": {
        "description": "The engine's clock: once an hour it clears the job folders past their time. Without it, "
                       "nothing stops, but old job folders stay.",
        "sources": [_ENGINE, _ours("app/worker")], "port": None, "shell": True},
    "portal-api": {
        "description": "The portal's API behind the web app: sign-in, uploads, documents, the tag manager and "
                       "administration. Without it, the site can't be used.",
        "sources": [_PORTAL, _ours("portal")], "port": 8080, "shell": True},
    "portal-worker": {
        "description": "Sends queued documents to the engine, collects the results, sends email, raises alerts and "
                       "clears expired files. Without it, documents wait and nobody is emailed.",
        "sources": [_PORTAL, _ours("portal/worker.py")], "port": None, "shell": True},
    "web": {
        "description": "Serves the web app's pages, and passes /api to the portal. Without it, the site doesn't load.",
        "sources": [_ours("Dockerfile.web"), _gh("nginxinc/docker-nginx-unprivileged", "nginx (unprivileged)")],
        "port": 8080, "shell": True},
    "verapdf": {
        "description": "Checks documents against PDF/UA-1 and PDF/UA-2, before and after. Without it, the engine "
                       "falls back to veraPDF's command line, which is slower.",
        "sources": [_gh("veraPDF/veraPDF-rest", "veraPDF REST"), _gh("veraPDF/veraPDF-library", "veraPDF")],
        "port": 8080, "shell": True},
    "music-reader": {
        "description": "Reads printed music into MusicXML, on demand: it runs only while there's music to read. "
                       "Without it, the AI describes the music like any other picture.",
        "sources": [_ours("docker/audiveris"), _gh("Audiveris/audiveris", "Audiveris")], "port": None, "shell": True},
    "formula-reader": {
        "description": "Reads each formula a second time from its picture, on demand, to compare with the AI's "
                       "reading. Without it, formulas are checked as before.",
        "sources": [_ours("docker/formula"), _gh("RapidAI/RapidLaTeXOCR", "RapidLaTeXOCR")], "port": None,
        "shell": True},
    "litellm": {
        "description": "The AI gateway: every AI call goes through it, to local or cloud models, with their keys, "
                       "limits and spend. Without it, documents with an AI level fail at the start; documents "
                       "without AI carry on.",
        "sources": [_gh("BerriAI/litellm", "LiteLLM"), _ours("config/litellm.yaml")], "port": 4000,
        "shell": True},
    "postgres": {
        "description": "The portal's database: people, groups, documents, pages and the audit log. Without it, "
                       "the portal stops.",
        "sources": [_gh("postgres/postgres", "PostgreSQL"), _gh("docker-library/postgres", "its image")],
        "port": 5432, "shell": True},
    "redis": {
        "description": "The engine's queue and the readers' request lists. Without it, no document moves.",
        "sources": [_gh("redis/redis", "Redis"), _gh("docker-library/redis", "its image")], "port": 6379,
        "shell": True},
    "seaweedfs": {
        "description": "File storage (S3-compatible): uploads, results and saved versions. Without it, nothing can "
                       "be uploaded or downloaded.",
        "sources": [_gh("seaweedfs/seaweedfs", "SeaweedFS")], "port": 8333, "shell": True},
    "cloudflared": {
        "description": "The Cloudflare Tunnel that brings the site's traffic in. Without it, the site can't be "
                       "reached from outside.",
        "sources": [_gh("cloudflare/cloudflared", "cloudflared")], "port": None, "shell": False},
    "prometheus": {
        "description": "Collects the service's metrics, for this page, Grafana and the alerts. Without it, this page "
                       "and the alerts about pods go quiet; documents carry on.",
        "sources": [_gh("prometheus/prometheus", "Prometheus"), _ours("deploy/k8s/monitoring/prometheus.yml")],
        "port": 9090, "shell": True},
    "grafana": {
        "description": "Dashboards of the service's metrics, reached through the portal. Without it, only the "
                       "dashboards are missing.",
        "sources": [_gh("grafana/grafana", "Grafana"), _ours("deploy/k8s/monitoring/dashboards")], "port": 3000,
        "shell": True},
    "kube-state-metrics": {
        "description": "Tells Prometheus what the cluster says about this namespace's pods and workloads. Without "
                       "it, this page can't list the pods.",
        "sources": [_gh("kubernetes/kube-state-metrics", "kube-state-metrics")], "port": 8080, "shell": False},
    "redis-exporter": {
        "description": "Gives Prometheus Redis's metrics. Without it, only those metrics are missing.",
        "sources": [_gh("oliver006/redis_exporter", "redis_exporter")], "port": 9121, "shell": False},
    "postgres-exporter": {
        "description": "Gives Prometheus the database's metrics. Without it, only those metrics are missing.",
        "sources": [_gh("prometheus-community/postgres_exporter", "postgres_exporter")], "port": 9187,
        "shell": True},
}


def about(name: str) -> dict | None:
    """The entry for a workload, or None for one this service doesn't know."""
    return CATALOG.get(name)
