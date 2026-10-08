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
| `VERAPDF_FLAVOURS` | `ua1,ua2` | The profiles checked (UA-2 is for information) |
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
| `PORTAL_TRUST_PROXY_HEADERS`, `PORTAL_CLIENT_IP_HEADER` | Behind a proxy: which header holds the caller's address (`cf-connecting-ip` behind Cloudflare; `x-client-ip` behind the optional Cloudflare Worker) |
| `PORTAL_S3_ENDPOINT_URL`, `PORTAL_S3_ACCESS_KEY`, `PORTAL_S3_SECRET_KEY`, `PORTAL_S3_BUCKET` | The object store (any S3-compatible service). In the bundled setups, `PORTAL_S3_SSE_KEK` is SeaweedFS's key for encryption at rest |
| `PORTAL_ENGINE_URL`, `PORTAL_ENGINE_API_KEY`, `PORTAL_ENGINE_MAX_IN_FLIGHT` | The engine, and how many documents are sent to it at once (4 by default) |
| `PORTAL_MAIL_*`, `PORTAL_SMTP_*`, … | Email ([details](email-and-branding.md)) |
| `PORTAL_BOOTSTRAP_SUPER_ADMINS` | Email addresses that become super admins at their first sign-in |
| `PORTAL_GATEWAY_ADMIN_URL` | The AI gateway's admin UI, linked for super admins (empty hides the link) |

## Changed in the app, not in settings

Super admins change these in **Administration → Settings** and **Services and AI levels**; they're stored in the
database and audited: branding, messages, the help address, upload limits, each person's queue, large documents,
"Let AI decide", notification and report defaults, session timeouts, retention, services, AI levels and kinds of
document. See the [super admin guide](../user-guide/super-admins/settings.md).

## After a change

Containers keep the settings they started with. After changing `.env` or `.env.portal`, recreate them (`docker
compose up -d`). In Kubernetes, `scripts/deploy.sh` restarts the pods whose settings file changed.
