# Configuration

[Operations](README.md) › Configuration

Everything is configured with environment variables, so the same images run anywhere. Locally they come from `.env`
(the engine stack) and `.env.portal` (the portal stack); in Kubernetes from two secrets made from
`deploy/k8s/engine.env` and `deploy/k8s/portal.env`. `.env.example` lists the engine settings an operator is likely
to change, with comments.

The complete list, every setting with its default and what it does, is the [settings reference](settings-reference.md),
generated from the code. This page is the overview: where settings live, the ones most often changed, and how they
fit together.

> **New 23 October 2026:** the settings reference (#588).

## The engine

> **Changed 22 October 2026:** `PORTAL_MALWARE_SCAN` moved to [the portal's table](#the-portal), where it's read.

| Setting | Default | |
|---|---|---|
| `REDIS_URL` | set by the compose file or manifests | The job queue |
| `DATA_DIR` | `/data` in the image | The shared job folder: the API and every worker must see the same one |
| `SERVICE_API_KEY` | empty | Required `X-API-Key` for every call; the portal's `PORTAL_ENGINE_API_KEY` is the same. The API won't start without it (#457) |
| `ALLOW_NO_API_KEY` | `false` | `true` lets the API start without `SERVICE_API_KEY`, open to anyone who can reach it: local development only |
| `PUBLIC_BASE_URL` | empty | Prefix for download URLs in the engine's answers; empty gives relative URLs |
| `MAX_UPLOAD_MB`, `JOB_TTL_HOURS`, `TASK_TIME_LIMIT_S` | 200, 24, 3600 | Upload size; how long job folders are kept (hours); the longest a job may run (seconds) |
| `MAX_PAGES` | 2000 | Documents with more pages are refused (0: no limit); keep it at or above the portal's |
| `PAGE_WORKERS`, `PAGE_WORKERS_MIN_PAGES` | 3, 50 | A long document's layout analysis and the checks' reading of each page are shared over this many processes (1: one), from this many pages (#124) |
| `WORKER_MAX_MEMORY_MB` | 1500 | A worker process that has grown past this after a job is replaced before its next one, so a long document's memory isn't carried into the next |
| `DEFAULT_LANGUAGE` | `en-US` | When neither the job nor the file says a document's language and its text is too short, or too mixed, to tell |
| `PDFUA_PART` | `1` | The PDF/UA part declared for PDF 1.x files, which can only be PDF/UA-1; the engine refuses anything else. A PDF 2.0 file is declared and judged as PDF/UA-2 whatever this says (#409) |
| `VERAPDF_FLAVOURS` | `ua1,ua2` | The profiles checked (UA-2 is for information; super admins can turn it off). For a PDF 2.0 file both always run: UA-2 gives the verdict, and UA-1's results drive the font and glyph repairs, whose rules it numbers |
| `VERAPDF_URL` | set by the compose file or manifests | veraPDF kept running ([below](#verapdf-service)); empty: the command line in the engine image only |
| `VERAPDF_CLI_HEAP` | `1g` | Java heap of the command line when it's used instead of the service (it runs inside the worker) |
| `OCR_ENABLED`, `OCR_LANGUAGE`, `OCR_ENGINE` | | Scanned pages ([details](ai-models.md#ocr-correction)) |
| `GATEWAY_BASE_URL`, `GATEWAY_API_KEY`, `GATEWAY_DEFAULT_MODEL` | | The AI gateway ([details](ai-models.md)) |
| `AI_GATEWAY_ONLY`, `GATEWAY_JUDGE_MODEL` | `false`, `local-judge` | Every AI call through the gateway: a provider named directly (the portal's fallback, the default judge) becomes the gateway's default or judge model. The Kubernetes manifests set it to `true`, since the engine has no other way out (#361) |
| `AI_CONCURRENCY`, `AI_TIMEOUT_S`, `AI_IMAGES_MAX`, `AI_TABLES_MAX`, `AI_ORDER_PAGES_MAX`, `AI_EXISTING_FIGURES_MAX` | | How much the AI is asked, and how fast ([limits](limits.md#what-the-ai-is-asked)) |
| `OLLAMA_*`, `LOCALAI_*`, `OPENROUTER_DEFAULT_MODEL`, `OPENROUTER_TABLE_MODEL` | | AI providers called directly, without the gateway (local testing) |
| `RASTER_DPI` | 200 | Resolution of page pictures sent to the AI and of flattened pages |
| `MUSIC_ENABLED`, `OMR_ENABLED`, `OMR_TIMEOUT_S`, `MUSIC_*` | on, off | Music examples found and tagged; read note by note with the music reader ([limits](limits.md#music)). On a cluster, set in `deploy.env` (`MUSIC_READER`) |
| `FORMULA_OCR_ENABLED`, `FORMULA_OCR_TIMEOUT_S`, `FORMULA_OCR_MAX` | off, 180, 200 | A second reading of each formula from its picture by the formula reader (#403). On a cluster, set by `FORMULA_READER` in `deploy.env` |
| `ALLOW_MOCK_PROVIDER` | `false` | `true` only for tests and the harness: the mock provider writes placeholder alt text |
| `VERIFY_*` | | Verification ([details](ai-models.md#verification-settings)) |
| `AI_NOTATION_*`, `OCR_AI_*` | | AI for notation and OCR ([details](ai-models.md)) |
| `OPENROUTER_API_KEY`, `CLASSIFIER_JEV_MODEL` | | The kind-of-document classifier, and direct OpenRouter calls |
| `ADOBE_*` | off | Adobe Autotag ([details](adobe-autotag.md)) |

## The AI gateway

`LITELLM_MASTER_KEY`, `LITELLM_DB_PASSWORD`, `UI_USERNAME`, `UI_PASSWORD`, and each model host's address and key
(`LOCAL_AI_BASE_URL`, `LOCAL_AI_API_KEY`, `OPENROUTER_API_KEY` …). See [AI models](ai-models.md#the-gateway).

## The portal

> **Changed 22 October 2026:** the malware-scanning settings are listed here (they're the portal's), and the
> portal's own Redis on a cluster is named.

| Setting | |
|---|---|
| `PORTAL_DATABASE_URL`, `PORTAL_REDIS_URL` | PostgreSQL and Redis. On Kubernetes the portal connects as its own rows-only account, `portal_app` (`PORTAL_APP_DB_PASSWORD`); migrations run as the owner (`PORTAL_DB_PASSWORD`). Its Redis is its own, `redis-portal` (`PORTAL_REDIS_PASSWORD`), holding only the rate limits, apart from the engine's queue (#472) |
| `PORTAL_JWT_PRIVATE_KEYS`, `PORTAL_SECRET_KEY` | Signing keys; generate with `python -m portal.cli genkeys` |
| `PORTAL_APP_URL`, `PORTAL_CORS_ORIGINS`, `PORTAL_COOKIE_SECURE` | The address people use; HTTPS cookies |
| `PORTAL_TRUST_PROXY_HEADERS`, `PORTAL_CLIENT_IP_HEADER` | Behind a proxy: which header holds the caller's address (`cf-connecting-ip` behind Cloudflare; `x-client-ip` behind the optional Cloudflare Worker; `x-forwarded-for` behind any other proxy or load balancer) |
| `PORTAL_TRUSTED_PROXIES` | With trusted proxy headers: the proxies' addresses (CIDRs, comma-separated). The header counts only on a connection from one of them; anyone else is known by their own address, so they can't set a header to get a fresh sign-in rate limit. Empty: every connection's header counts (only behind a proxy nothing can bypass). The Kubernetes deploy sets it from `TRUSTED_PROXY_CIDRS` |
| `PORTAL_PDFCHECK_MEMORY_MB`, `PORTAL_PDFCHECK_CPU_S`, `PORTAL_PDFCHECK_TIMEOUT_S` | An upload's pages are counted in a child process with these limits (1024 MB, 30 s of CPU, 60 s in all); a file past them is refused as too large or complex to open safely, and the API isn't affected |
| `PORTAL_TRUSTED_PROXY_HOPS` | With `x-forwarded-for`: how many trusted proxies add to it (1 by default). The caller's address is read that many places from the end, never from the front, which a caller can make up |
| `TRUSTED_PROXIES` (web container; `WEB_TRUSTED_PROXIES` in the compose file and `deploy.env`) | Addresses or CIDR ranges of proxies in front of the web app. It then passes on the caller's address from their `X-Forwarded-For`, not the proxy's own. Empty: the web app's peer is the caller |
| `PORTAL_S3_ENDPOINT_URL`, `PORTAL_S3_ACCESS_KEY`, `PORTAL_S3_SECRET_KEY`, `PORTAL_S3_BUCKET` | The object store (any S3-compatible service). In the bundled setups, `PORTAL_S3_SSE_KEK` is SeaweedFS's key for encryption at rest |
| `PORTAL_ENGINE_URL`, `PORTAL_ENGINE_API_KEY`, `PORTAL_ENGINE_MAX_IN_FLIGHT` | The engine, and how many documents are sent to it at once (4 by default) |
| `PORTAL_MAIL_*`, `PORTAL_SMTP_*`, … | Email ([details](email-and-branding.md)) |
| `PORTAL_BOOTSTRAP_SUPER_ADMINS` | Email addresses that become super admins at their first sign-in |
| `PORTAL_TURNSTILE_SITE_KEY`, `PORTAL_TURNSTILE_SECRET_KEY`, `PORTAL_TURNSTILE_FAIL_OPEN` | Cloudflare Turnstile, a bot check on "Email me a code" ([details](../deployment/cloudflare.md#a-bot-check-on-sign-in)). Off unless both keys are set. If Cloudflare can't be reached, sign-in is allowed (the rate limits still apply) unless `FAIL_OPEN` is `false` |
| `PORTAL_RATE_OTP_PER_EMAIL`, `PORTAL_RATE_OTP_PER_IP`, `PORTAL_RATE_VERIFY_PER_IP`, `PORTAL_RATE_WINDOW_SECONDS` | Rate limits on sign-in codes: 5 requests per address and 30 per IP address, and 50 code checks per IP address, every 15 minutes |
| `PORTAL_RATE_DOWNLOADS_PER_HOUR`, `PORTAL_RATE_EXPORTS_PER_HOUR`, `PORTAL_RATE_UPLOADS_PER_HOUR`, `PORTAL_RATE_DOCUMENT_EXPORTS_PER_HOUR` | Per person, per hour: files downloaded (600), report and person-data exports (120), upload requests (200; one can hold several files), zips of many documents (20; one each, however many files). Past one, a plain 429. 0 turns it off |
| `PORTAL_ENGINE_AI_PROVIDER`, `PORTAL_ENGINE_AI_MODEL` | The portal's fallback AI, for a document sent without an AI level (`localai`; empty model: the provider's default) |
| `PORTAL_REVIEW_FINDINGS_MAX` | Review items kept with a document (300; [limits](limits.md#checking-the-result)) |
| `PORTAL_ENABLE_DOCS` | Interactive API docs at `/api/v1/docs`; leave off in production |
| `PORTAL_PROMETHEUS_URL`, `PORTAL_GRAFANA_URL`, `PORTAL_METRICS_ENABLED` | Monitoring: where the portal reads Prometheus for alerts and Administration › System, Grafana's link, and the portal's own `/metrics` (on). Set by the Kubernetes deploy from `MONITORING` ([monitoring](monitoring.md#prometheus-and-grafana)) |
| `PORTAL_SIEM_MODE`, `PORTAL_SIEM_SYSLOG_*`, `PORTAL_SIEM_HTTPS_*`, `PORTAL_SIEM_PULL_TOKEN` | The audit log to the campus SIEM: by syslog over TLS, by HTTPS, or a pull API ([details](siem.md)). Off by default |
| `PORTAL_BACKUP_EXPECTED` | Alert when no nightly backup has finished. Set by the Kubernetes deploy from `BACKUPS=cronjob` |
| `PORTAL_MALWARE_SCAN`, `PORTAL_CLAMD_HOST`, `PORTAL_CLAMD_PORT`, `PORTAL_CLAMD_TIMEOUT_SECONDS`, `PORTAL_MALWARE_SIGNATURES_DAYS` | Uploads and help-desk attachments scanned by ClamAV before they're stored: `off` (the default) or `clamav`, at `clamav:3310`; signatures older than 3 days are an alert ([malware scanning](malware-scanning.md)). On a cluster, set by `MALWARE_SCAN` in `deploy.env` |
| `PORTAL_GATEWAY_ADMIN_URL` | The AI gateway's admin UI, linked for super admins. Empty (the default) shows them how to reach it by port-forward instead of a link; set it only to an address their browsers can reach |

## On a cluster: `deploy.env`

> **New 22 October 2026:** the cluster-wide settings an operator is most likely to look for, with where each is
> explained.

On Kubernetes, `deploy/k8s/deploy.env` holds the deployment's own settings (no secrets): the cluster, sizes, storage
and the optional pieces. `scripts/deploy.sh` sets the engine's and portal's settings above from it where they
overlap. Every one is in `deploy/k8s/deploy.env.example`, grouped in
[`deploy.env` at a glance](../../deploy/k8s/README.md#deployenv-at-a-glance). The ones that change what runs, or
what can leave the cluster:

| Setting | Default | |
|---|---|---|
| `INGRESS_CONTROLLER`, `INGRESS_CLASS` | `nginx`, empty | `traefik` or `nginx` (ingress-nginx): the Ingresses get that controller's own settings ([Traefik's settings](../deployment/first-cluster/middleware.md#traefiks-settings-for-the-cluster)) |
| `CERT_ISSUER`, `INGRESS_TLS_SECRET` | empty | HTTPS at your own ingress controller: a cert-manager ClusterIssuer, or a Secret with your certificate |
| `EGRESS_PROXY`, `EGRESS_ALLOW`, `CLUSTER_CIDRS` | `off` | Outbound traffic from the portal, the AI gateway, the Adobe worker and ClamAV: direct (`off`), through the egress proxy recording each host (`log`), or only to the hosts this deployment uses (`enforce`) ([the egress proxy](../../deploy/k8s/README.md#outbound-traffic-the-egress-proxy)) |
| `MALWARE_SCAN` | `off` | `clamav` runs ClamAV and scans every upload ([malware scanning](malware-scanning.md)) |
| `NODE_ENCRYPTION` | `check` | Whether traffic between the nodes is encrypted, checked before each deploy: `check` shows it, `require` stops a deploy without it, `off` doesn't look |
| `ENGINE_NODES` | empty | A node label, `key=value`: the pods that open uploaded documents run only on those nodes ([scaling](../deployment/first-cluster/scaling.md#4-the-pdf-engine-on-its-own-nodes)) |
| `GATEWAY_ENGINE_BUDGET` | empty | What the engine's own gateway key may spend, USD per 30 days |
| `MONITORING`, `BACKUPS`, `AUTOSCALE` | `on`, `external`, `auto` | [Monitoring](monitoring.md#prometheus-and-grafana), [nightly backups](backups-and-migrations.md#nightly-backups-on-the-cluster), [scaling with the queue](#scaling-with-the-queue) |

## Changed in the app, not in settings

Super admins change these in **Administration → Settings** and **Services and AI levels**; they're stored in the
database and audited: branding, messages, the help address, upload limits, each person's queue, large documents,
"Let AI decide", notification and report defaults, session timeouts, retention, services, AI levels and kinds of
document. See the [super admin guide](../user-guide/super-admins/settings.md).

## After a change

Containers keep the settings they started with. After changing `.env` or `.env.portal`, recreate them (`docker
compose up -d`). In Kubernetes, `scripts/deploy.sh` restarts the pods whose settings file changed.

## veraPDF service

veraPDF checks every document before and after remediation, every saved version and the PDF report. Run from the
command line, each check starts Java, which takes about a second and up to 600 MB of memory. So veraPDF also runs
as a service that stays running (its REST server, the `verapdf/rest` image). The engine sends each check there, with
the same results ([the comparison](../evaluation/test-reports/2026-10-09-lighter-validators.md)).

- **Settings:** `deploy/verapdf/validator.xml` makes the service report up to 200 failed checks per rule with
  their messages, as the command line does; the engine reads pages, fonts and glyph widths from them. The Java heap
  is capped (`VERAPDF_HEAP`, 512 MB), and the pod's memory limit is `VERAPDF_MEMORY_LIMIT` (1 GiB). That's
  enough for ordinary documents; documents of several hundred pages need more: about `VERAPDF_HEAP=3g` and
  `VERAPDF_MEMORY_LIMIT=4Gi` (a 300-to-500-page document ran 512 MB out of memory in production).
- **Restarts:** on Kubernetes, a scheduled job restarts it every hour (`VERAPDF_RESTART_SCHEDULE`), starting the
  new pod before stopping the old one. The job's account can restart that one deployment and nothing else.
- **If it doesn't answer**, that check runs from the command line in the engine image instead, and the engine logs
  a warning. Jobs don't fail because of it. The command line's Java heap is `VERAPDF_CLI_HEAP` (1 GB): it runs
  inside the worker, beside the document. When the service ran out of memory on a file, the check isn't retried
  there (a smaller heap would fail too, and once took the worker down with it); the report says veraPDF ran out of
  memory, and that check counts as unavailable. The engine's health check (`GET /api/v1/health`) shows `verapdf_service`.
- **Super admins choose** under Settings › Uploads and limits: the service or the command line, and whether to also
  check PDF/UA-2.
- **Both profiles at once:** with the service, a file's PDF/UA-1 and PDF/UA-2 checks run side by side (a 500-page
  document is checked six to eight times, so this saves a minute or more). Two large checks in one pod measured
  1.2 GiB together, well within `VERAPDF_MEMORY_LIMIT=4Gi`. The command line, used when the service isn't, still
  runs one check at a time: it shares the worker's memory.

## Scaling with the queue

On Kubernetes, engine workers, veraPDF and the AI gateway can grow and shrink with the work
([deploy/k8s/README.md](../../deploy/k8s/README.md#scaling-with-the-queue)). Two portal settings go with it:

| Setting | Default | |
|---|---|---|
| `PORTAL_ENGINE_MAX_IN_FLIGHT` | 4 | Documents kept at the engine at once, at least. When the engine's running workers have more places (`GET /api/v1/capacity`), the portal uses that number |
| `PORTAL_ENGINE_IN_FLIGHT_CEILING` | 64 | Never more than this at the engine, however many workers answer |

Each service and AI level can also have **Most documents at once**, set by super admins.
