# Configuration

[Operations](README.md) › Configuration

Everything is configured with environment variables, so the same images run anywhere. Locally they come from `.env`
(the engine stack) and `.env.portal` (the portal stack); in Kubernetes from two secrets made from
`deploy/k8s/engine.env` and `deploy/k8s/portal.env`. `.env.example` lists every engine setting with comments.

## The engine

| Setting | Default | |
|---|---|---|
| `REDIS_URL` | set by the compose file or manifests | The job queue |
| `DATA_DIR` | `/data` in the image | The shared job folder: the API and every worker must see the same one |
| `SERVICE_API_KEY` | empty | Required `X-API-Key` for every call (set it in any shared deployment) |
| `MAX_UPLOAD_MB`, `JOB_TTL_HOURS`, `TASK_TIME_LIMIT_S` | 200, 24, 3600 | Upload size; how long job folders are kept (hours); the longest a job may run (seconds) |
| `DEFAULT_LANGUAGE` | `en-US` | When a document's language can't be detected |
| `VERAPDF_FLAVOURS` | `ua1,ua2` | The profiles checked (UA-2 is for information; super admins can turn it off) |
| `VERAPDF_URL` | set by the compose file or manifests | veraPDF kept running ([below](#verapdf-service)); empty: the command line in the engine image only |
| `OCR_ENABLED`, `OCR_LANGUAGE`, `OCR_ENGINE` | | Scanned pages ([details](ai-models.md#ocr-correction)) |
| `GATEWAY_BASE_URL`, `GATEWAY_API_KEY`, `GATEWAY_DEFAULT_MODEL` | | The AI gateway ([details](ai-models.md)) |
| `VERIFY_*` | | Verification ([details](ai-models.md#verification-settings)) |
| `AI_NOTATION_*`, `OCR_AI_*` | | AI for notation and OCR ([details](ai-models.md)) |
| `OPENROUTER_API_KEY`, `CLASSIFIER_JEV_MODEL` | | The kind-of-document classifier, and direct OpenRouter calls |
| `ADOBE_*` | off | Adobe Autotag ([details](adobe-autotag.md)) |

## The AI gateway

`LITELLM_MASTER_KEY`, `LITELLM_DB_PASSWORD`, `UI_USERNAME`, `UI_PASSWORD`, and each model host's address and key
(`LOCAL_AI_BASE_URL`, `LOCAL_AI_API_KEY`, `OPENROUTER_API_KEY` …). See [AI models](ai-models.md#the-gateway).

## The portal

| Setting | |
|---|---|
| `PORTAL_DATABASE_URL`, `PORTAL_REDIS_URL` | PostgreSQL and Redis |
| `PORTAL_JWT_PRIVATE_KEYS`, `PORTAL_SECRET_KEY` | Signing keys; generate with `python -m portal.cli genkeys` |
| `PORTAL_APP_URL`, `PORTAL_CORS_ORIGINS`, `PORTAL_COOKIE_SECURE` | The address people use; HTTPS cookies |
| `PORTAL_TRUST_PROXY_HEADERS`, `PORTAL_CLIENT_IP_HEADER` | Behind a proxy: which header holds the caller's address (`cf-connecting-ip` behind Cloudflare; `x-client-ip` behind the optional Cloudflare Worker; `x-forwarded-for` behind any other proxy or load balancer) |
| `PORTAL_TRUSTED_PROXY_HOPS` | With `x-forwarded-for`: how many trusted proxies add to it (1 by default). The caller's address is read that many places from the end, never from the front, which a caller can make up |
| `TRUSTED_PROXIES` (web container; `WEB_TRUSTED_PROXIES` in the compose file and `deploy.env`) | Addresses or CIDR ranges of proxies in front of the web app. It then passes on the caller's address from their `X-Forwarded-For`, not the proxy's own. Empty: the web app's peer is the caller |
| `PORTAL_S3_ENDPOINT_URL`, `PORTAL_S3_ACCESS_KEY`, `PORTAL_S3_SECRET_KEY`, `PORTAL_S3_BUCKET` | The object store (any S3-compatible service). In the bundled setups, `PORTAL_S3_SSE_KEK` is SeaweedFS's key for encryption at rest |
| `PORTAL_ENGINE_URL`, `PORTAL_ENGINE_API_KEY`, `PORTAL_ENGINE_MAX_IN_FLIGHT` | The engine, and how many documents are sent to it at once (4 by default) |
| `PORTAL_MAIL_*`, `PORTAL_SMTP_*`, … | Email ([details](email-and-branding.md)) |
| `PORTAL_BOOTSTRAP_SUPER_ADMINS` | Email addresses that become super admins at their first sign-in |
| `PORTAL_TURNSTILE_SITE_KEY`, `PORTAL_TURNSTILE_SECRET_KEY`, `PORTAL_TURNSTILE_FAIL_OPEN` | Cloudflare Turnstile, a bot check on "Email me a code" ([details](../deployment/cloudflare.md#a-bot-check-on-sign-in)). Off unless both keys are set. If Cloudflare can't be reached, sign-in is allowed (the rate limits still apply) unless `FAIL_OPEN` is `false` |
| `PORTAL_RATE_OTP_PER_EMAIL`, `PORTAL_RATE_OTP_PER_IP`, `PORTAL_RATE_VERIFY_PER_IP`, `PORTAL_RATE_WINDOW_SECONDS` | Rate limits on sign-in codes: 5 requests per address and 30 per IP address, and 50 code checks per IP address, every 15 minutes |
| `PORTAL_GATEWAY_ADMIN_URL` | The AI gateway's admin UI, linked for super admins. Empty (the default) shows them how to reach it by port-forward instead of a link; set it only to an address their browsers can reach |

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
  is capped (`VERAPDF_HEAP`, 512 MB), and the pod's memory limit is `VERAPDF_MEMORY_LIMIT` (1 GiB).
- **Restarts:** on Kubernetes, a scheduled job restarts it every hour (`VERAPDF_RESTART_SCHEDULE`), starting the
  new pod before stopping the old one. The job's account can restart that one deployment and nothing else.
- **If it doesn't answer**, that check runs from the command line in the engine image instead, and the engine logs
  a warning. Jobs don't fail because of it. The engine's health check (`GET /api/v1/health`) shows `verapdf_service`.
- **Super admins choose** under Settings › Uploads and limits: the service or the command line, and whether to also
  check PDF/UA-2.

## Scaling with the queue

On Kubernetes, engine workers, veraPDF and the AI gateway can grow and shrink with the work
([deploy/k8s/README.md](../../deploy/k8s/README.md#scaling-with-the-queue)). Two portal settings go with it:

| Setting | Default | |
|---|---|---|
| `PORTAL_ENGINE_MAX_IN_FLIGHT` | 4 | Documents kept at the engine at once, at least. When the engine's running workers have more places (`GET /api/v1/capacity`), the portal uses that number |
| `PORTAL_ENGINE_IN_FLIGHT_CEILING` | 64 | Never more than this at the engine, however many workers answer |

Each service and AI level can also have **Most documents at once**, set by super admins.
