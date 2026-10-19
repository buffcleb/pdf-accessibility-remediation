# Build log

[Design notes](README.md) › Build log

What was built, in order. Each entry records what was done, with the details that mattered (endpoints,
migrations, edge cases, checks). Issue and pull request numbers refer to the project's development history.

## Contents

- [Phase 1: done (portal API foundation)](#phase-1-done-portal-api-foundation)
- [Phases 2–4: done (jobs, quota, web app); demo stack running](#phases-24-done-jobs-quota-web-app-demo-stack-running)
- [Known gaps (next)](#known-gaps-next)
- [Tag viewer and human-reviewed versions: done](#tag-viewer-and-human-reviewed-versions-done)
- [Services and per-service quotas: done (phase 1 of the services plan)](#services-and-per-service-quotas-done-phase-1-of-the-services-plan)
- [AI gateway and AI tiers: done (phase 2 of the services plan)](#ai-gateway-and-ai-tiers-done-phase-2-of-the-services-plan)
- [Adobe Autotag: done (phase 3 of the services plan)](#adobe-autotag-done-phase-3-of-the-services-plan)
- [Backups: done](#backups-done)
- [Specialist notation read by AI (E1, #17): done](#specialist-notation-read-by-ai-e1-17-done)
- [AI-assisted OCR correction (E2, #18): done](#ai-assisted-ocr-correction-e2-18-done)
- [Email providers and branding (#6, #5): done](#email-providers-and-branding-6-5-done)
- [Administration cleanup, part A: Attention and access checks (#14): done](#administration-cleanup-part-a-attention-and-access-checks-14-done)
- [Administration cleanup, part B: one admin area (#14): done](#administration-cleanup-part-b-one-admin-area-14-done)
- [Member approval (#7): done](#member-approval-7-done)
- [Help requests, part A: asking and talking (#11): done](#help-requests-part-a-asking-and-talking-11-done)
- [Help requests, part B: resolving (#11): done](#help-requests-part-b-resolving-11-done)
- [Live reports, part A (#12): done](#live-reports-part-a-12-done)
- [Live reports, part B (#12): done](#live-reports-part-b-12-done)
- [Queues: view and pause (#31): done](#queues-view-and-pause-31-done)
- [Large documents mixed into the queue (#32): done](#large-documents-mixed-into-the-queue-32-done)
- [Scheduled email reports, part A (#13): done](#scheduled-email-reports-part-a-13-done)
- [Scheduled email reports, part B (#13): done](#scheduled-email-reports-part-b-13-done)
- [Email notification settings (#35): done](#email-notification-settings-35-done)
- [Not emailing what someone already saw in the portal (#36): done](#not-emailing-what-someone-already-saw-in-the-portal-36-done)
- [AI levels and kinds of document (#26): done](#ai-levels-and-kinds-of-document-26-done)
- [My documents: paging, search and filters (#48): done](#my-documents-paging-search-and-filters-48-done)
- [Document page, part 1 (#50, #51, #52, #54, #55): done](#document-page-part-1-50-51-52-54-55-done)
- [Document page, part 2: the report as an accessible PDF (#49, #53): done](#document-page-part-2-the-report-as-an-accessible-pdf-49-53-done)
- [Upload form (#47): done](#upload-form-47-done)
- [Kind-of-document classifier (#43): done](#kind-of-document-classifier-43-done)
- [Kubernetes deployment (#62): done](#kubernetes-deployment-62-done)
- [Portal additions, 8 to 11 October: done](#portal-additions-8-to-11-october-done)
- [Tag viewer, 9 to 14 October: done](#tag-viewer-9-to-14-october-done)
- [Music notation (#134): done](#music-notation-134-done)
- [Reviews of 12 October, and their fixes: done](#reviews-of-12-october-and-their-fixes-done)
- [Portal, 13 October: done](#portal-13-october-done)
- [Engine: notation, structure and footnotes, 12 to 15 October: done](#engine-notation-structure-and-footnotes-12-to-15-october-done)
- [Long documents in production, 15 and 16 October: done](#long-documents-in-production-15-and-16-october-done)
- [Super admins: cost, limits and re-runs (#312 to #327): done](#super-admins-cost-limits-and-re-runs-312-to-327-done)
- [Operations and hardening, 17 October: done](#operations-and-hardening-17-october-done)
- [People and sign-in, 17 October: done](#people-and-sign-in-17-october-done)
- [Engine: documents of every kind, 17 October: done](#engine-documents-of-every-kind-17-october-done)
- [Tag manager, 17 and 18 October: done](#tag-manager-17-and-18-october-done)
- [Monitoring (#397, #398): done](#monitoring-397-398-done)
- [Math as MathML (#403): done](#math-as-mathml-403-done)
- [PDF/UA-2 for PDF 2.0 documents (#409): done](#pdfua-2-for-pdf-20-documents-409-done)
- [Large documents on more than one core (#124): done](#large-documents-on-more-than-one-core-124-done)

## Phase 1: done (portal API foundation)
Code is in `portal/`, tests in `tests/portal/`, and the image is `Dockerfile.portal`. The demo stack
is `docker-compose.portal.yml`, layered on the engine stack.

| Area | Endpoints (under `/api/v1`) |
|---|---|
| Sign-in | `POST /auth/code`, `POST /auth/code/verify`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/logout-all` |
| Self | `GET /me` |
| Group admin | `GET /groups`, `GET /groups/{id}`, `GET /groups/{id}/users`, `PATCH /groups/{id}/users/{uid}` (status, cap), `GET /groups/{id}/audit` |
| Super admin | `GET/POST /admin/groups`, `GET/PATCH /admin/groups/{id}`, `POST/DELETE /admin/groups/{id}/domains[/{domain}]`, `GET/POST/DELETE /admin/groups/{id}/admins[/{uid}]`, `GET /admin/users`, `PATCH /admin/users/{id}` (name, status, move group), `POST/DELETE /admin/users/{id}/super-admin`, `GET/PATCH /admin/settings`, `GET /admin/audit` |
| Health | `GET /health` |

Security measures in place and tested (33 tests against real Postgres):
- **Sign-in codes:**
  - stored only as HMAC hashes
  - single use, newest code only
  - expire after 10 minutes
  - 5 wrong attempts burn the code
  - code requests are rate-limited per email address and per IP address
- **Sessions:**
  - ES256 access tokens, with `kid` so keys can be rotated
  - forged, `alg:none`, HS256 and tampered tokens are all rejected
  - the session is checked on every request, so sign-out, disabling a user and disabling a group
    take effect at once
- **Refresh tokens:**
  - rotated on every use
  - reusing an old one after a 20-second grace period revokes the session
  - the refresh and sign-out endpoints refuse other origins
  - idle and absolute session timeouts
- **Access control:**
  - a test covers every role against every endpoint
  - group admins get 404 for other groups, so group IDs can't be probed
  - an admin can't disable themselves, and a group admin can't disable a super admin
  - the last super admin can't be removed, with row locks against races
- **Data checks:**
  - email domains are unique across groups
  - user search escapes SQL wildcards
  - client IP headers are validated
- **Records:** the audit table is append-only, enforced by a database trigger.
- **The image:**
  - runs as non-root, on a read-only filesystem, with all capabilities dropped
  - refuses to start without signing keys and a secret
  - API docs are off unless enabled

Not yet done: Postgres row-level security (planned for phase 6, hardening), and the records purge
job (phase 2, with the file retention sweeper).

## Phases 2–4: done (jobs, quota, web app); demo stack running
Start the portal demo:
```bash
docker compose -p portal -f docker-compose.portal.yml --env-file .env.portal up -d --build
```
- Web app: http://localhost:8088
- Mail catcher (sign-in codes and job emails): http://localhost:8025
- The stack is independent of the engine stack and calls the engine at `PORTAL_ENGINE_URL`.

What was added:
- **Storage:** S3 through boto3. The demo runs SeaweedFS with **server-side encryption at rest**
  (the key-encryption key is in `.env.portal`). Keys follow
  `groups/{group}/users/{user}/jobs/{job}/…` and contain IDs only. The bucket is private, and
  downloads stream through the API, which checks permission and records any download by someone
  other than the owner.
- **Uploads:**
  - each file is accepted or refused on its own
  - size and page limits come from settings
  - checks for a PDF signature, password protection and damage
  - pages are counted with pikepdf
  - the running-jobs limit is enforced under a row lock
- **Quota:**
  - an append-only ledger (a trigger refuses updates)
  - reserve on upload, commit on completion, release on failure or cancel
  - user caps may add up to more than the pool, but the pool still applies
  - over-quota messages are configurable globally and per group, with a ticketing link
  - top-ups require a ticket number, or an override with a comment, which is flagged in the ledger,
    reports and audit log
  - corrections are recorded as adjustments, and the pool can't go below what's already used
- **Worker** (`python -m portal.worker`):
  - submits queued jobs to the engine (at most `PORTAL_ENGINE_MAX_IN_FLIGHT` at a time)
  - polls progress and copies the results into storage
  - stores a result summary (verdict, before and after counts, review findings) in Postgres
  - emails the user when a job is done or has failed
  - deletes files after the retention period and purges records on the configured cycle
- **Reports:** CSV exports for usage, ledger and sign-ins (per group or for all groups) and a
  groups summary. Spreadsheet-formula injection is neutralised, and every export is audited.
- **Web app** (`web/`, React + Vite + TypeScript, no inline scripts, strict CSP):
  - sign-in with an emailed code, and the configured "not provisioned" and "disabled" messages
  - my documents: pages left, upload by drag-and-drop or file picker, audit-only option, job list
    that updates itself
  - document page: verdict, the two-file explanation, downloads, review list, rendered report
  - manage group: overview with the ledger, users with editable caps and disabling, documents,
    CSV reports, activity
  - administration: groups (create, domains, admins, top-up with ticket or override, adjustments,
    settings, disable), users (move, disable, super admin), all-group reports, settings, audit log
  - accessibility: skip link, labeled fields, focus moved to the content on navigation, live
    regions for status, visible focus, works in dark mode
- **Deployment:**
  - `Dockerfile.web`: unprivileged nginx with security headers that proxies `/api/`
  - `web/wrangler.jsonc` and `web/public/_headers`: Cloudflare Workers static assets
  - the same `dist/` folder serves from S3 + CloudFront
  - all containers run read-only with all capabilities dropped

Tests: 51 portal tests, including a 10-thread race against the pool, failure refunds, retention,
the records purge, CSV injection and file-access scoping. With the 68 engine tests that makes 119.

## Known gaps (next)
- **SAML** (phase 5). On hold.
- **Engine changes:** the spec's engine-reads-S3 and signed-webhook design isn't built yet. The
  worker currently polls the engine and copies files over HTTP, which works but moves each file
  twice.
- **Hardening** (phase 6):
  - Postgres row-level security
  - ~~a separate, non-owner database role for the app, so it can't disable the append-only triggers~~: done on
    the cluster (#337)
  - ~~counting pages in a sandboxed process rather than in the API process~~: done as a limited child process (#356)
  - SeaweedFS filer and IAM authentication (the demo relies on the private Docker network)
  - ~~scans in CI~~: done as dependency audits, Bandit's SQL check, Dependabot, and Trivy in `scripts/build.sh`
    (#228, #326, #362)
- ~~Per-group AI policy~~: done as **local-only groups** plus **AI tiers** (see below). Jobs without a tier
  still use `PORTAL_ENGINE_AI_PROVIDER`.

## Tag viewer and human-reviewed versions: done
- **Engine:**
  - Every job writes a tag map for the original and for each output (`reports.tag_maps`): element
    boxes, roles, alt text and language, in the same element numbering the review findings use.
  - `POST /api/v1/edit` applies tag edits and re-validates the result: tag integrity (a failure fails
    the save), semantic checks, a judge review of rewritten alt text only, veraPDF, and a
    pixel-identity check.
  - The edits: tag type and heading level, alt text, decorative, language, table header cell and
    scope, reading-order moves, **merge** (join neighboring tags, e.g. a formula broken into pieces)
    and **split** (move a tag's pieces from a chosen point into a new tag). Code in
    `app/tagging/editor.py`. Tag maps list each tag's pieces (`parts`) so the viewer can offer split
    points.
- **Portal:**
  - `document_versions` holds the original, Remediated and Visually exact versions plus every
    Human reviewed N version.
  - The engine's outputs are never changed.
  - The owner and group admins can edit; super admins who aren't the group's admin can only view.
  - Saving is free. At most 3 saves per document can be in progress at once.
  - Admin access to tag maps and PDFs is audited.
- **Web:** `/jobs/{id}/tags` shows:
  - two panes with synced scrolling, each with its own version picker; the left pane is read-only
  - zoom from 50% to 300% (fit width by default), remembered per browser
  - overlays for flagged tags, all tags, or reading order
  - details on hover or focus
  - an editor panel with a keyboard-accessible reading-order list
  - pending changes with undo, saved as a new version
  - "Show" links from the review list that open the viewer at the flagged element

## Services and per-service quotas: done (phase 1 of the services plan)
Pages are now counted **per service**. The standard engine is the first service; Adobe Autotag and AI tiers
come later, using the same rules (both are now done).

- **Opt-in at three levels:**
  - A super admin switches a service on (Administration → Services and AI options).
  - A group admin enables it for the group (the group's Services and pages).
  - Then the group admin gives it to everyone in the group or person by person.
  - People choose a service at upload, from the ones they've been given.
- **Pages at three levels:**
  - an optional global pool per service
  - each group's pool for the service
  - a cap per person (their own, or the group default)

  A document reserves its pages at all three levels at once, or is refused with the level that ran out.
  Top-ups and corrections take a service (and for the global pool, the service's own endpoints), with the
  same ticket/override rules. The ledger records the service and the scope (global or group) of every entry.
- **Data handling:**
  - Each service is marked as local or as using an external provider.
  - Groups can be **local-only**, which blocks every external provider whatever else is switched on.
  - A notice can be shown at upload, switched on or off per service, with its own text.
- **Cost guards per service:**
  - most pages per document
  - most pages sent per day (documents over the cap wait in the queue for the next day, or until the cap is
    raised; they don't fail)
  - confirm before send: documents are accepted as *held*, nothing is reserved, and people confirm or cancel
    each one with its page count
- **API:**
  - `GET /services`: what the signed-in user can pick
  - `GET/PATCH /admin/services[/{key}]` and `POST /admin/services/{key}/topups|adjustments`
  - `PATCH /groups/{id}` (local-only) and `PATCH /groups/{id}/services/{key}`
  - `PUT /groups/{id}/users/{uid}/services/{key}`
  - `POST /jobs/{id}/confirm`
  - Uploads take `processing` and `confirm`
- **Super admins and people's pages:**
  - set or clear anyone's limit for any service, in the group's People tab (group admins do the same for their own members)
  - **correct someone's usage**: a negative number gives pages back, a positive one charges more. A reason is
    required and a ticket is optional. The person's, the group's and the service's used counts move together,
    and a `correct` ledger entry is written.
  - corrections are super-admin only
  - both are recorded once with the group attached, so they appear in the super admin's audit log and the group's
    Activity tab, marked as a super admin's change (`quota.cap_set`, `quota.usage_corrected`)
- **Migration 0005** moves each group's pool and each user's cap and usage into the standard service, and
  drops the old columns. It can't be downgraded automatically: back up the database first.

## AI gateway and AI tiers: done (phase 2 of the services plan)
- **LiteLLM** runs in the engine's stack (`litellm` and `litellm-db` in docker-compose.yml). Its models are set in
  `config/litellm.yaml`, with hosts and keys from the environment.
  - `local-vision` and `local-judge` point at the local OpenAI-compatible server.
  - Cloud, larger and commercial models are commented examples.
  - `max_budget` caps spend per budget period as a backstop to the portal's page quotas.
  - **Admin UI** at `http://localhost:4000/ui`, bound to the host only, with sign-in `UI_USERNAME`/`UI_PASSWORD`
    or the master key. It shows models (with a playground), spend per model, key and tag (jobs are tagged
    `job:<id>`, `tier:<key>`), request logs and errors, and manages virtual keys with their own budgets and
    rate limits. Outside local testing, keep it behind VPN or SSO.
- **The engine** talks to the gateway with the `gateway` provider.
  - Each call carries the job and AI tier as LiteLLM metadata/tags.
  - The cost LiteLLM reports for each call is added up into `metrics.ai_cost_usd`, and the review's cost into
    `verification_ai_cost_usd`.
  - `GET /api/v1/ai/models` lists the gateway's models, so the portal never needs LiteLLM credentials.
- **AI tiers are services** of kind `ai_tier`, each pointing at a gateway model.
  - A super admin publishes one from the gateway's model list (Administration → Services and AI options → Add an AI option);
    new tiers start switched off.
  - Tiers follow the same opt-in, local-only, notice, quota and cost-guard rules as processing services.
  - A group's **preselected** tier is used when the uploader doesn't pick one; with none, the engine's default
    AI is used, as before.
  - A document is charged on its processing service and its tier (an unmetered tier, like the seeded
    **Local AI**, counts use without limiting it).
  - The job's AI cost is recorded and shown to admins on the job page.
- Each tier also has a **review model** (judge) from a different model family. A document's AI review and
  later re-reviews of its versions use its tier's judge, falling back to the engine's `VERIFY_JUDGE_MODEL`.
  Migration 0008 adds it and sets Local AI's to `local-judge`.
- **Review every figure** (full review) can be asked for at upload, when saving a version, or when re-reviewing one.
  Each AI tier can switch it off (`allow_full_review`), e.g. for an expensive commercial tier. Migration 0009.
- Migration 0007 seeds the **Local AI** tier (`ai:local` → `local-vision`, local, unmetered, off until
  switched on).

## Adobe Autotag: done (phase 3 of the services plan)
- **Engine route `adobe`** (`POST /api/v1/remediate` with `route=adobe`): refused unless `ADOBE_ENABLED=true`
  and never with `report_only`. Jobs go to the Celery queue `adobe`, served by the `adobe-worker` service.
  - The engine calls Adobe's REST API directly with one credential: token (cached), upload, Autotag, status
    polls, download of the tagged PDF and Adobe's report, then deletion of the assets.
  - Adobe's limits aren't published, so requests per minute, documents at once, pages and MB per request,
    retries, polling and timeouts are settings. The first two are shared by all Adobe workers through
    Redis, and a "too many requests" answer pauses them all. Running out of transactions isn't retried.
  - Larger documents are split, tagged in parts and merged back onto the original (bookmarks, links,
    forms and metadata kept). In tests the merged result matches tagging the whole file.
  - The output is Adobe's file unchanged (`_adobe_tagged.pdf`), with veraPDF before and after, tag
    integrity and review, the tag map, `reports.adobe_reports` and `metrics.adobe` (pages sent, parts,
    requests, retries, seconds). Structural problems in Adobe's tags are reported, not fatal.
- **Portal service "Adobe Autotag"** (migration 0011): a processing service with `engine_route = adobe`,
  seeded switched off, external, metered, with a notice and confirm-before-send. It can't be marked local.
  - It uses no AI tier and no "review every figure"; audits always use the standard route and no pages.
  - The result is the version **Adobe tagged**; Adobe's xlsx report is kept with the report files.
  - The job's Adobe use is recorded in `cost.adobe` and shown to admins. Adobe failures have their own
    messages, and the pages are given back.
- **Testing without Adobe:** `app/adobe/fake.py` is a stand-in that speaks Adobe's protocol and tags with
  the engine's own tagger (and can throttle, run out of transactions or fail on request). Tests use it
  in-process; the `fake-adobe` compose profile runs it as a server. The harness has `--route adobe`.
- **AI alt text for Adobe** (migration 0012). At upload, "Write alt text for images with AI" is on by
  default for Adobe and uses the chosen AI tier. The engine describes every Figure Adobe left without alt
  text and applies the descriptions as tag edits to a separate copy (`files.enhanced`). The copy must render
  identically to Adobe's file, is validated and AI-reviewed, and becomes the version **Adobe + AI alt
  text** (version kind `enhanced`). If the AI wasn't needed (no figures) or every call failed, the tier's
  pages are given back. `GET /services` gives each processing service an `ai_use` of `full` or `alt_text`.
- **Font fixes in the improved copy (#10):** ToUnicode maps, incomplete glyph lists (7.21.4.2) and glyph
  widths (7.21.5, only if pixel-identical) are fixed in the separate copy, with or without AI alt text.
  The version is named after what was done: **Adobe + AI alt text**, **Adobe + fixes**, or **Adobe + AI alt
  text and fixes** (`reports.improvements`). The standard route now removes incomplete glyph lists from its
  visually exact file too, instead of flattening pages for them.

## Backups: done
- `scripts/backup.sh` backs up the portal database and, by default, the LiteLLM database. Flags add stored
  files (`--files`, read through the portal's S3 settings), config (`--config`, plaintext by default,
  `--encrypt-config` to encrypt), retention (`--keep N`) and an off-machine copy (`--upload s3://...`).
  Each backup has a manifest and checksums.
- `scripts/restore.sh` checks the checksums, then restores only what's named. It saves the current
  database first and stops and restarts the services around a database restore. It has a dry run and
  can restore from S3.
- Both use `python -m portal.cli` commands (`export-files`, `import-files`, `upload-backup`,
  `download-backup`). Details are in [Operations](../operations/backups-and-migrations.md).

## Specialist notation read by AI (E1, #17): done
- The engine sends notation the layout rules can't read to the job's AI model. A Formula or a short text
  block is sent when it has:
  - characters with no Unicode meaning, big operators or roots, or accents
  - a chemical formula or reaction, or dosage and prescription notation
  - engineering units and symbols (in formulas)
- Fragments of one displayed equation are joined into one Formula. The reading is used only if the AI's
  transcription matches the page's letters and digits.
- Each reading gets a review finding ("read-aloud text written by AI"), and Formula readings go to the
  AI reviewer.
- Per AI tier: **Read specialist notation with AI** (`allow_notation`, migration 0013, on by default);
  the portal sends `notation_ai: false` when it's off.

## AI-assisted OCR correction (E2, #18): done
- OCR comes from the Tesseract command line when it's installed: per-word confidence, and lines split at
  wide gaps so table cells line up.
- The AI corrects uncertain words and transcribes lines Tesseract couldn't read, under checks that keep
  the text true to the scan. Every change is flagged for review.
- Per AI tier: **Correct OCR with AI** (`allow_ocr_ai`, migration 0014, on by default); the portal sends
  `ocr_ai: false` when it's off.

## Email providers and branding (#6, #5): done
- **Email backends:**
  - `smtp`, with TLS/STARTTLS and authentication errors reported
  - `ses` (API), `plunk` (HTTP API) and `cloudflare` (Email Service REST API)
  - `console` and `memory`
- Temporary failures are retried with backoff. Sign-in codes are sent before answering, so a failure gets a
  clear "we couldn't send your code" (503) instead of a code that never arrives.
- Super admins can see the provider and send a test email (Administration → Settings → Email); every test is
  audited.
- **Branding** (runtime settings; no migration), public at `GET /api/v1/branding` so the sign-in page can
  show it:
  - the service and organization names
  - a logo (raster only, ≤ 512 KB, with alt text; served at `/api/v1/branding/logo`)
  - a help email address
  - an accent color, checked for 4.5:1 contrast against white
  - the email sender name

  They're used in the header, sign-in page, footer, page titles and emails.
- **Not yet:** bounce and complaint webhooks.

## Administration cleanup, part A: Attention and access checks (#14): done
- **Attention** (`GET /admin/attention`, and `GET /groups/{id}/attention` for group admins):
  - empty or low pools (group and global); personal limits larger than the pool left
  - held, daily-capped, stuck and failed documents; AI unavailable
  - each item says what to do. It's the first page of Administration and the top of a group's Overview.
- **Why can't this person use a service?** (`GET /admin/access-check?email=&service=`, and
  `/groups/{id}/access-check` for a group's own members): every check in order, with the first failure's
  fix and the pages left.
- **Refusals say which level ran out** ("Your group has 0 Adobe Autotag pages left and this document needs
  10."), followed by the administrators' message.

## Administration cleanup, part B: one admin area (#14): done
- **One area for both roles** (`/admin`): a sidebar that follows the role. Group admins see Attention, their
  group(s) and the guided set-up; super admins also see services, people, reports, settings and the audit
  log. Old `/manage/...` links redirect to the same group.
- **A group's workspace** (`/admin/groups/{id}/...`): Overview, Services and pages, People, Documents,
  Activity, Reports and Group settings.
  - **Services and pages** is one table per group. `GET /groups/{id}` now gives each service
    `people_with_access` and `own_limits`.
  - Personal limits are edited only in the group's **People** tab, one service at a time; usage corrections
    stay in People (all groups), for super admins.
  - Group admins can't add pages: they get **Request pages**, which opens the support link or email.
- **Service detail pages** (`/admin/services/{key}`): General, Data and notice, Pages and cost guards (with
  the global pool), Model or Adobe connection, and Groups. One save per service.
- **Guided set-up** (`/admin/offer`): service and group, who gets it, pages, default limit, then a check
  that one of the people can use it. It uses the existing endpoints, in order.
- **Unsaved changes:** forms that hold edits ask before the page is left (in-app links, Back, reload, close,
  sign-out), with one dialog for the whole app. The tag editor uses the same question.
- **People pages:** one **Manage** button per person opens a panel with the less frequent actions (access
  check and account in a group; move, correct usage, account and super admin across groups).
- **Settings** has a tab per topic (Branding, Help and messages, Email, Uploads and limits, Sign-in and
  data), each saving only its own fields.
- Access checks look people up by their stored address, so an address that fails today's validation (an
  older or test one) can still be checked.
- An access token signed by a key the portal no longer has (after a rotation or a restart) now gets a 401
  ("sign in again") instead of a server error.
- No migration.

## Member approval (#7): done
- **Per group:** *New members need approval* (`groups.require_approval`). Groups created from now on take
  the super admins' default (`new_groups_require_approval`, on); existing groups were left off, so nothing
  changed for them. Group admins and super admins can switch it in Group settings.
- **Separate from the account status:** `users.approval` is `pending`, `approved` or `declined` (with when,
  by whom, and an optional reason). Sign-in and sessions work as before; people waiting can sign in.
  Existing accounts are approved. Admins (group or super) are always treated as approved, and making
  someone an admin approves them.
- **Refused at the API, not just hidden:** uploads, confirming held documents, tag edits and AI reviews
  return `403 pending_approval` or `403 approval_declined` (with the reason). Nothing is reserved. The
  access check has an "Approved by the group" step.
- **Deciding:** `POST /groups/{id}/users/{uid}/approve` and `/decline` (`{reason}`), for the group's admins and
  super admins; others get 404. Decline only applies to people waiting; a declined person can be approved
  later. `GET /groups/{id}/users?approval=pending` lists who's waiting.
- **Notifications:** one email to the group's admins per request (to the super admins if the group has no
  admins), with a link to the People tab; an email to the person when approved or declined (with the
  reason). Emails go after the response, so a slow provider doesn't hold anything up. A daily digest for
  busy groups isn't built yet.
- **Shown:** a badge with the count on Administration, the sidebar and the group's People tab
  (`me.pending_approvals`, `group.pending_members`); an Attention item per group; a Waiting list at the top of
  People.
- **Audit and reports:** `user.approval_requested`, `user.approved` and `user.declined` in the group's
  Activity; the usage CSV gains `approval`, `approval_date` and `approved_by`.
- Migration 0015. SAML sign-ins (when built) go through the same provisioning, so they follow the same rule.

## Help requests, part A: asking and talking (#11): done
Spec and decisions: issue #11. People ask for help with a finished document; super admins work through a
queue and talk it over with them.
- **Tables (migration 0016):** `help_requests` (document, person, group, kind, status, version, page, tag,
  who's handling it, saved, read and email-batching timestamps), `help_messages` (message, internal note, or
  event) and `help_attachments`. One active request per document (unique partial index).
- **Statuses:** open → in progress → waiting on the person → resolved or closed. Only super admins change them;
  a reply from the person while it's waiting moves it back to in progress. Events are stored as codes and
  worded for the reader (the person sees "We need something from you", not the admin's address).
- **Access:** the person, their group's admins and super admins read and write; internal notes and attachments on
  them are for super admins only; others get 404. Group admins can't change status.
- **Routes:** `POST/GET /jobs/{id}/help`, `GET /help/mine`, `GET /help/{id}`, `POST /help/{id}/messages`
  (multipart, `internal`), `GET /help/{id}/attachments/{aid}`, `POST /help/{id}/status`, `POST /help/{id}/assign`,
  `GET /admin/help?saved=`, `GET /groups/{id}/help`. `me` gains `help_unread` and `help_needs_us`; the owner's
  document list gains each document's `help`.
- **Attachments:** PDF, PNG, JPEG or UTF-8 text, checked by content; 10 MB per file and 50 MB per request
  (settings), up to 5 per message. Stored under `help/{group}/{request}/` rather than the document's folder, so a
  request saved past retention keeps them. Downloads go through the API with the same checks; opening someone
  else's file is audited.
- **Emails:** new requests to every super admin; replies to the other side (the handler, or all super admins
  while nobody has taken it; the person for super-admin and group-admin replies and status changes). Messages
  within 10 minutes go out as one email: the first at once, the rest from the worker.
- **Limits and retention:** 5 new requests per person per day, 30 messages per hour. Requests expire with the
  document's files unless saved.
- **Audit:** `help.requested`, `help.message` (without the text), `help.status`, `help.assigned`,
  `help.attachment_opened`.

## Help requests, part B: resolving (#11): done
- **Resolve** (`POST /help/{id}/resolve`: `outcome` resolved or closed, `note`, `version_number`, `refund_pages`):
  the note is posted to the person and kept as the resolution; a resolved request needs one. The version must be
  one of the document's (not the original, not failed). Pages go back through `quota.correct_usage` for each
  service the document was charged to (skipping an AI tier whose pages were already given back), up to what it
  cost. Migration 0017 adds `resolution`, `resolved_version_number`, `pages_refunded`, `finished_at` and
  `reminders_sent`.
- **Corrected files** (`POST /help/{id}/corrected`, super admins): a PDF with the same number of pages becomes a
  new version of kind `corrected` ("Corrected by support N"), queued like a saved edit. The worker sends it to the
  engine's `/edit` with no edits and the version it replaces as `reference`; the engine checks it as it is
  (integrity, veraPDF, tag map) and makes the visual comparison against the reference. Failed checks show on
  the version; they don't block. Support sees the upload in the thread; the person sees the version once it's
  linked.
- **Reopen** (`POST /help/{id}/reopen`): the person, within `help_reopen_days` (14) of it finishing; super admins
  any time. Back to in progress, and support is told.
- **Saved** (`POST /help/{id}/save`): kept past retention, listed in the Saved view; audited.
- **Waiting on the person:** reminder emails after 3 and 7 days without a reply, then closed at 10 with an email
  saying they can reopen. Runs with the worker's sweep.
- Settings: `help_max_file_mb`, `help_max_request_mb`, `help_reopen_days` (Settings → Uploads and limits).

## Live reports, part A (#12): done
- **One engine, six reports** (`portal/live_reports.py`): documents, usage, page ledger, sign-ins, help requests and
  the audit log. Each is one SQL query declared as columns (with kind, totals, default visibility, super-admin-only
  cost), filters, search fields and a default sort.
- **`GET /reports/{name}`** (any admin): `since`, `until`, `q`, `sort` (`-key` for descending), `page`, `page_size`
  (25 by default, up to 200), `cols`, `group_id`, and the report's own filters as plain parameters. Returns the page of rows (with
  the ids the app needs for links), `total_rows`, totals and headline numbers over every matching row, and the
  filter options. Viewing isn't audited.
- **`GET /reports/{name}.csv`**: the same query, every row (up to 100,000), only the chosen columns; audited as
  `report.exported` with the filters, search and scope.
- **Access:** super admins see everything (or `group_id`); group admins their groups, or one of them; other groups get
  404. Cost columns are super-admin only.
- **Indexes** (migration 0018): documents and ledger by date, the audit log by group and date, help requests by date.
- **The app** keeps the whole view in the URL: Administration → Reports and each group's Reports tab. The audit log
  is one of the reports (the sidebar's Audit log and a group's Activity tab now redirect to it). The old CSV
  endpoints are unchanged.

## Live reports, part B (#12): done
- **Four more reports**, on the same engine, with grouping (`group_by`) and an optional chart definition:
  - `quality`: per period (`by` = hour, day, week or month; when not given, it follows the date range: hour up to
    2 days, day up to 62, week up to a year, otherwise month) and processing option (service + AI option): documents,
    passed, pass rate, flagged, average failed checks, pages.
  - `failures`: remaining failures from each document's result summary, by clause: documents and checks.
  - `cost` (super admins): per period, group and option: documents, pages, AI cost, Adobe pages.
  - `daily` (super admins): pages charged per UTC day and service, with the service's daily cap and percent used.
- Super-admin-only reports return 404 to group admins and are left out of `GET /reports`.
- **Charts:** the response carries `chart` (kind line, bar or horizontal bar; x; series; measures; points
  aggregated over every matching row, not just the page) and `period`. The app draws one measure at a time (no
  second axis), evenly spaced time with gaps left as gaps, a legend for two or more series, the daily limit as a
  reference line when one service is shown, and a keyboard- and hover-readable crosshair. Colors are a fixed
  categorical set checked for color-blind separation, light and dark. The table stays the full record.
- **The app** groups the reports under three tabs (Documents, Usage and cost, Activity) with the group's reports as
  buttons beneath, and shows **By** beside the period rather than under Filters.
- No migration.

## Queues: view and pause (#31): done
- **Queues** (`portal/queues.py`): one per service ("service:<key>": documents whose processing service or AI option it
  is), `edits` (saved edits), `reviews` (AI reviews of saved versions) and `mail` (the outbox). Counts: waiting, in
  progress, failed in the last 7 days, oldest waiting; email also sent in the last 7 days.
- **Pause** (`queue_pauses`, one row while paused, with who, when and an optional note): the worker starts nothing new
  from it; work already started finishes. A document waits while any service it uses is paused (progress stage
  `paused`, shown to the person); it isn't rerouted. A review waits while its document's AI option is paused.
- **Email outbox** (`mail_outbox`): results, help-request and approval emails are queued in the same transaction
  and sent by the worker. Temporary failures are tried again with a growing wait (30 s doubling, up to 15 min) up to
  `PORTAL_MAIL_ATTEMPTS`; then, or for a permanent failure, the email is failed. Sent and canceled emails are kept
  30 days; failed ones with the other records. **Sign-in codes and test emails don't use the outbox** and can't
  be paused.
- **Single items:** cancel a waiting document (pages released, the person emailed), save, review or email; try a
  failed one again (a document's pages are reserved again on the person's own limits and the request is refused,
  with the reason, if they no longer can; a save only if the document has no newer version).
- **API** (super admins): `GET /admin/queues`, `GET /admin/queues/{key}/items?state=waiting|active|failed` (up to
  200), `POST /admin/queues/{key}/pause` (`note`), `POST …/resume`, `POST …/items/{id}/retry`, `POST …/items/{id}/cancel`.
  Audited: `queue.paused`, `queue.resumed`, `job.retried`, `job.canceled` (by support), `version.retried`,
  `version.canceled`, `version.review_retried`, `version.review_canceled`, `mail.retried`, `mail.canceled`.
- **Attention:** super admins see each paused queue and emails that failed in the last 7 days; group admins see how
  many of their documents wait for a paused service.
- Migration 0019 (`queue_pauses`, `mail_outbox`).

## Large documents mixed into the queue (#32): done
- Instead of holding large documents for off hours (costs aren't time-of-day dependent), the worker mixes them in
  so the queue never jams.
- **Settings** (Settings → Uploads and limits): `large_doc_pages` (more than this is large; 200; 0 = off),
  `large_doc_every` (one large after every N smaller started; 5), `large_doc_at_once` (large documents in progress at
  once; 1).
- **Picking** (`worker._pick_queued`): paused services and daily caps hold documents first; then, oldest first,
  smaller documents start, and a large one starts when fewer than `large_doc_at_once` are running and either
  `large_doc_every` smaller ones have started since the last large one (counted from the database, so it survives
  restarts) or nothing smaller can start. Per-person and engine limits apply to both.
- Waiting large documents show progress stage `waiting_large` (the person sees why). Notes from a pause or limit
  that no longer applies are cleared.
- **Process now** (super admins, Queues): `POST /admin/queues/{key}/items/{id}/process-now` sets `expedited_at`/
  `expedited_by`; the document goes first and skips the large-document rules (not pauses or daily caps). Audited
  as `job.expedited`. Trying a failed document again clears it.
- **Attention:** large documents waiting more than 2 hours.
- Automatic only: people don't choose. Migration 0020 (`jobs.expedited_at`, `jobs.expedited_by`).

## Scheduled email reports, part A (#13): done
- **Reports** (`portal/email_reports.py`), each for those it applies to when it's sent: `my_month` (everyone approved
  in a group), `group_summary` (group admins: each group they look after, no cost), `service_summary` (super admins).
  All monthly by default; each person can choose monthly, weekly, daily or off (`report_subscriptions`; no row =
  monthly). Disabled accounts and lost roles get nothing.
- **Periods** in one service time zone (`report_time_zone`, default UTC) at `report_send_hour` (default 7):
  daily = the day before; weekly = Monday to Sunday, sent Monday; monthly = the month before, sent on its first
  working day (Monday to Friday). `email_reports_enabled` switches them all off.
- **Once per period:** the worker checks every minute; each (person, frequency, period) is recorded in
  `report_sends` (unique), so a restart never resends. A missed send catches up once if it's recent (daily 20 h,
  weekly 3 days, monthly 7 days), so switching this on mid-month doesn't email last month. Reports with the same
  frequency go in one email. Nothing to report for `my_month` (no documents, nothing waiting): no email, recorded
  as skipped.
- **The email:** HTML (headings, tables with header cells and captions, notes in words, no images) and the same in
  plain text; sent through the outbox (`mail_outbox.html`, all four mail backends). Links to the live reports and
  the group. Footer: why they get it, a link to Settings, and one unsubscribe link per report.
- **Unsubscribe:** `/unsubscribe?t=…` in the app; the token is `base64(user.kind)` signed with an HMAC of the
  secret key. `GET /email-reports/unsubscribe?t=` only describes it (mail scanners open links);
  `POST /email-reports/unsubscribe` turns that one report off. No sign-in needed. Audited as `email_report.changed`
  with `how`.
- **API:** `GET`/`PUT /me/email-reports`, `POST /me/email-reports/sample` (last month's, now, marked "Sample", at most
  one a minute, not recorded as the scheduled send).
- **Super admins** see every send in Reports → Activity → **Report emails** (to, period, reports, delivery from the
  outbox, problem). Settings → Email has the switch, time zone and hour.
- Migration 0021 (`report_subscriptions`, `report_sends`, `mail_outbox.html`).
- Part B, below. Still open: group defaults set by admins.

## Scheduled email reports, part B (#13): done
- **Saved views** (`saved_reports`, `portal/report_views.py`): owner, name, report, params (search, sort, columns and
  the report's filters; the date range, page and group aren't saved), group, frequency (monthly, weekly, daily),
  recipients (user ids), `attach_csv`. Made with **Email this view** on a report.
- **Access, at send time:** the view's scope is the owner's now (every group for a super admin, the view's group, or
  the group admin's groups); if the owner can't see it any more it isn't sent. A recipient must be active and a super
  admin, or (for a group-scoped, non-super report) an admin of every group in the scope. The same rule decides who
  can be chosen (`GET /me/report-views/recipients`).
- **Sending:** with the built-in reports (every minute, same periods, time zone and catch-up). The view is run for the
  period just ended; one email per view, recipient and period (`report_sends.saved_report_id`, unique per view, person
  and period; the built-in sends keep their own unique index). The email: headline numbers, the first 50 rows with
  the app's labels and the report's time zone, totals, a link to the view for the period, and a stop link.
- **CSV attachment:** every row (as Export CSV), up to 5 MB, through the outbox (`mail_outbox.attachments`). Sent by
  the smtp and ses backends (SES as a raw MIME message); with plunk and cloudflare the email says to download it.
- **API:** `GET/POST /me/report-views`, `PATCH`/`DELETE /me/report-views/{id}` (owner), `POST …/leave`,
  `POST …/sample` (now, for the last period, one a minute). The unsubscribe link takes `view:<id>` and removes
  that recipient. Audited: `report_view.created`, `.changed`, `.deleted`, `.left` (with how).
- Migration 0022 (`saved_reports`, `report_sends.saved_report_id`, `mail_outbox.attachments`).

## Email notification settings (#35): done
- **Kinds** (`portal/notifications.py`): `documents` (outbox kind `job`: done, failed, canceled by support), `help`
  (`help`: replies, status changes, reminders; staff notices), `admin` (`approval`: someone waiting; admins only).
  Each is `each`, `daily` or `off`, per person (`users.notify_prefs`); without a choice, the service default
  (`notify_default_documents|help|admin`, Settings → Email). Choice changes are audited (`notifications.changed`).
- **Never affected:** sign-in codes and test emails (not queued), the person's own approval decision (kind
  `account`), scheduled reports and samples (their own settings).
- **How:** `outbox.queue` asks `notifications.status_for`: `off` doesn't queue it; `daily` stores it `held`.
  The worker (every minute) combines each person's held emails from before the latest summary time (the report
  hour in the report time zone) into one `digest` email, and marks them `combined` (purged after 30 days with sent
  ones).
- **API:** `GET`/`PUT /me/notifications`. Settings page: Email notifications.
- Migration 0023 (`users.notify_prefs`, `mail_outbox` statuses `held` and `combined`).

## Not emailing what someone already saw in the portal (#36): done
- **What's open:** the app posts `POST /me/seen {items}` every 30 s while a page is in view (`visibilityState`),
  and when it comes back into view: `job:<id>` (a document's page), `help:<id>` (a help request, on the document's
  page or support's request page), `jobs` (My documents). Stored in `user_presence` (one row per person and item,
  latest time), kept a day.
- **Emails about something** carry `mail_outbox.about` (document results and support cancellations: `job:<id>`;
  help-request notices: `help:<id>`; reminders and closing notices don't) and wait `SEEN_DELAY` (2 minutes) before
  sending. When due, if the person had it open (or `jobs` for a document) since 45 s before the email was made,
  it's not sent: status `seen`. Held emails are checked the same way when the daily summary is made.
- **Setting, per kind:** `notify_prefs.even_if_seen = {documents, help}`. Defaults: help requests skipped when
  seen (chatting would flood the inbox), documents still emailed (people keep them as a record). A checkbox under
  each of those rows in Settings → Email notifications; `GET`/`PUT /me/notifications` (`{choices}`, each with
  `even_if_seen`; `PUT` takes `even_if_seen: {category: bool}`); audited.
- Migration 0024 (`user_presence`, `mail_outbox.about`, status `seen`).

## AI levels and kinds of document (#26): done
- **Levels:** the AI services (kind `ai_tier`) are called AI levels everywhere people read (upload, Services, groups,
  reports, messages). Each keeps its own page pool, limits and rules, as before.
- **Kinds of document** (`ai_kinds`: level, key, label, description, gateway model, optional review model, enabled,
  order): super admins add them on a level's **Kinds of document** tab (`POST/PATCH/DELETE
  /admin/services/{key}/kinds[/{kind}]`, audited `service.kind_added/changed/removed`). General isn't a row: it's the
  level's own models. Nothing changes for a level until a kind is added.
- **Upload:** `GET /services` lists each level's kinds (General first; names and descriptions only). `POST /jobs`
  takes `ai_kind`; an unknown or switched-off kind is refused (422 `unknown_kind`). The job records `ai_kind`
  (None = General).
- **Processing:** the worker sends the kind's model, and its review model or else the level's. If the kind is gone,
  switched off, or its model isn't served by the gateway (checked at most once a minute), the level's General
  models are used and `ai_kind_fallback = "unavailable"`. If the engine fails with `ai_provider_unavailable`, the job
  goes back in the queue once with General (`"failed"`, audited `job.ai_fallback`), pages still reserved; a second
  failure fails as usual. The document page says when General was used.
- **Visibility:** people and group admins see the level and kind by name (`JobOut.ai_label`, reports' "Service and AI
  level" and "Kind of document"); model names only on super-admin pages.
- Migration 0025 (`ai_kinds`, `jobs.ai_kind`, `jobs.ai_kind_fallback`). Follow-up: #43 (a classifier to suggest the
  kind).

## My documents: paging, search and filters (#48): done
- **`GET /documents`** (the signed-in person's own): `q` (file name, case-insensitive; `%` and `_` literal), `status`
  (held, waiting, in_progress, done, failed, canceled), `result` (passes, needs_work, review = has findings, help =
  an open help request), `service` (processing service or AI level), `audit`, `since`/`until`, `sort` (`created_at`,
  `filename`, `pages`, `result`; `-` for descending; newest first by default), `page`, `page_size` (≤200). Returns
  `{items, total, page, page_size, active}`; `active` (theirs still waiting or in progress, any filter) tells the app
  to keep refreshing. `GET /jobs` is unchanged.
- **The app** keeps the view in the address, sorts by column heading (`aria-sort`), and links to documents with
  back state ("← Back to My documents"). The documents table is shared with the group's Documents tab.

## Document page, part 1 (#50, #51, #52, #54, #55): done
- **Layout:** a summary header (name, facts, verdict badge, "Failed checks: before → after", the main download and
  Open in tag viewer; while processing, a five-step indicator mapped from the engine's progress stages), one-line
  notes with "Why?", three tiles (fixed, to review, still failing) that open their tab, and two columns: the tabs,
  and a side column with Downloads and help. One column on a phone; no sideways scrolling.
- **Tabs** (`?tab=review|report|check|versions`): accessible tab pattern (arrow keys, Home and End, roving tab
  index). Needs review is the default when there's something to review.
- **Needs review** (#50): a wrapping list instead of a table; above 8 items, severity buttons with counts, a search
  (text, or a page number) and 25 per page (`?sev=`, `?rq=`, `?rp=`).
- **Back to the same place** (#51): Show records `#finding-<n>` on the current address, then opens the tag viewer
  with back state; the viewer's Back (and the browser's) return to it, and the item is scrolled into view, focused
  and briefly highlighted.
- **Report sub-tabs** (#54): the engine's Markdown report grouped by heading into Summary, Changes, Still failing,
  For review and Details (`?section=`), with a download link. The PDF report is part 2 (#49, #53).
- Web only: no API change.

## Document page, part 2: the report as an accessible PDF (#49, #53): done
- **Engine** (`app/reports/pdf.py`): from the structured result (not the Markdown), semantic HTML (one h1, sections
  as h2, tables with header cells and captions, `lang`, title) written as tagged **PDF/UA-1** by WeasyPrint, then
  checked with veraPDF (`report_pdf_compliant` in the result; a problem never fails the job). Sections: Summary,
  What changed, Accessibility check (PDF/UA-1 rules before and after, still-failing first, pages), Still failing,
  For review (up to 150 items), Details. Audit-only documents get the audit version (no after). Never names
  models. Written next to the Markdown and JSON reports for the standard and Adobe routes; the API returns it as
  `reports.report_pdf`.
- **Branding:** the portal sends `report_branding` (service name, organization, accent color, the AI level by
  name) in the job's flags.
- **Portal:** stores it as file kind `report_pdf` ("Report (PDF)"), shown in Downloads under the document's PDFs;
  the Report tab's and the Accessibility check's download links give it (Markdown or XML only for documents
  without one). The XML, Markdown and JSON stay under Technical files.
- **Engine image and CI:** Pango (`libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`) and `weasyprint`.

## Upload form (#47): done
- **Form:** files are listed (with Remove); one line per choice: **Processing** (a select, when there's more than
  one) and **AI** ("Premium AI · Math and science", with **Change** for the batch). **Change AI** on a file gives it
  its own level and kind. **Review every figure** is under **More options**. **Check only** sits next to
  **Remediate** (replacing the "Audit only" checkbox), with a line saying what a check uses.
- **Per file:** `POST /jobs` takes `per_file`, JSON `{"<file index>": {"ai_tier", "ai_kind"}}`; each choice is checked
  like the batch's, and a bad one refuses the upload (422) before any file is taken. Hold, external services and
  charging are worked out per file.
- **Group AI** (`groups.ai_mode`, `ai_level`, `ai_kind`): "choose" (default) or "fixed". Fixed needs a level the
  group has switched on (422 `level_required`) and a kind it offers (422 `unknown_kind`); uploads then use them
  whatever is asked. Group admins set it in Group settings, **AI for uploads**. Group services list each level's
  kinds (`kinds`, names only).
- **External marks** (`groups.show_external`, default off): the upload form marks external options only when the
  group turns it on. `GET /upload-options` gives the form `ai_mode`, the fixed level and kind, and `show_external`.
- **Audits per AI level** (`services.audit_ai`, AI levels only, set on the level's pages tab): `free` (default, as
  before), `charge` (the document's pages are reserved on the level only, not on Standard) or `off` (no AI: the job
  has no level, `jobs.no_ai`, and the engine gets provider `none` with the AI review, notation and OCR AI off;
  `ai_label` "No AI"). `GET /services` gives each level's `audit_ai` so the form can say what a check uses.
- Migration 0026.

## Kind-of-document classifier (#43): done
- **"Let AI decide"** (`ai_kind = "auto"`, offered after General when the level has kinds and the group can use a
  classifier): the job is stored with `ai_kind_auto`, and before it's sent the worker asks the engine
  (`POST /api/v1/classify`, file + kinds) which kind it is. At or above `classifier_threshold` (default 0.6) the kind
  is set; otherwise General. `jobs.ai_kind_pick` keeps `{kind, label, confidence, used, by, reason}` (`unsure`,
  `no_text`, `failed`, `unavailable`); a failure never holds the document. Groups can also set "Let AI decide" as
  their fixed kind (422 `classifier_unavailable` when there's no classifier for them). `auto` can't be a kind's key.
- **Backends** (runtime settings): `classifier_jev` (Jev through OpenRouter; external: the job gets `classifier` in
  `external_services` and the page says the text was read outside; never for local-only groups) and
  `classifier_local_model` (a gateway model, for local-only groups and when Jev is off). `classifier_pages` (default
  3): the first pages, plus pages spread through long documents (a thesis's equations come after its front matter).
- **Engine** (`app/classify.py`): text as the layout rules read it (math in its spoken form), capped at 12,000
  characters, plus counts (specialist notation, math symbols, Greek letters, lines with scripts, tables, figures,
  form fields, blanks to fill in) and the title. Jev is asked a `choice` with General first (it leans towards the
  first option, and General is the safe answer); a gateway model is asked for `{"kind", "confidence"}`. Jev's cost is
  added to the job's cost (`classifier_usd`).
- Comparison on the 30 sample documents: Jev 30/30, `local-vision` 29/30, total Jev cost $0.0016.
- Migration 0027 (`jobs.ai_kind_auto`, `jobs.ai_kind_pick`).

## Kubernetes deployment (#62): done
- `scripts/build.sh` builds the engine, portal and web images for linux/amd64 and pushes them, tagged with the
  commit. `scripts/deploy.sh` makes the settings and secret files (with new keys), then applies the secrets,
  Postgres, Redis and SeaweedFS, a migration Job and the rollout. Manifests are in `deploy/k8s/manifests`.
- Follow-ups: pods restart when their settings file changes (#66); the job folder check fixed (#65); a setting to
  turn off the AI gateway's shared admin login (#118); the gateway link hidden unless an address is set (#115).
- **Scaling with the queue** (#112): engine workers follow the portal's queue through KEDA's PostgreSQL scaler;
  veraPDF and the AI gateway get CPU autoscalers; each service can cap documents at once (migration 0032).
- **veraPDF as a service** (#64): kept running instead of started per check, after a comparison of validators
  ([report](../evaluation/test-reports/2026-10-09-lighter-validators.md)).

## Portal additions, 8 to 11 October: done
- **Mail:** Date and Message-ID always set, which fixed DKIM failures (#67); the sign-in code kept out of the
  subject (#70); Cloudflare's backend sends attachments (#63). **Email log** (#69, migration 0031): every email
  recorded, sign-in codes and test emails without their body, under Reports › Activity.
- **Break-glass sign-in code** from the command line (#71), audited.
- **Groups allow single addresses** (#95, migration 0030): a listed address joins the group without its domain,
  and comes before a domain match.
- **Turnstile bot check** on "Email me a code" (#68), off unless its keys are set.
- **The caller's address** behind any proxy, not only Cloudflare (#84).
- **AI usage report** per AI level and model (#80): tokens, cost and failed calls for writing and review.
- **Early removal** (#75, migration 0029): people mark a document done and its files go after a hold (24 hours);
  super admins remove files or delete documents at once.
- **Queues:** stop items in progress and remove failed ones; a job whose worker was killed is reported as failed
  (#126, migration 0033).
- **Failed documents get a help request** of their own, opened by the service (#147, migration 0034).
- **Time by stage:** each job records where its time went, shown in the report and the report PDF (#138, #148).
- **Limits:** one reference page, [limits](../operations/limits.md), and the report says when a document reaches
  one (#157); figures described per document became a setting, 1,000 by default (#159).
- **Public snapshots** (#86): `scripts/publish.sh` exports a ref and refuses to publish on gitleaks findings,
  private addresses or private-looking files.

## Tag viewer, 9 to 14 October: done
- **Viewing:** one page at a time, with the reading order in the tree's place (#119); read the tags aloud in
  reading order with the browser's voices (#105); tags from the uploaded document marked original (#129); long
  documents open a page at a time (#150). Many layout changes followed (#245 to #275): resizable panes, a tag
  menu, isolate a tag, select several.
- **Editing:** drag tags in the tree, only where valid (#108); change the area a tag covers and tag untagged
  content (#93); delete a tag, choosing what happens to what's inside (#130, #146); several tags made one list,
  or a list built from a paragraph's lines (#250, #253); a list made a table of contents (#276); a figure split
  by choosing its pieces on a picture (#270 to #272); "Describe with AI" for a part of a figure (#273, migration
  0037); a table cell's headers seen and changed (#278).
- **Saving:** unsaved changes kept on the server between sessions (#151, `tag_drafts`, migration 0035); save into
  the same reviewed version, with undo for a day (#152, migration 0036).
- Renamed **Tag manager** (#294), with its guide in its own folder (#296).

## Music notation (#134): done
- Each example of music is one figure. Staves are found on a low-resolution rendering, and an optional
  Audiveris service, started on demand through a Redis list and KEDA, reads them note by note.
- One figure per line of music, then per measure; readings stop at a whole measure; the whole piece as one
  MusicXML file; the summary says how the music was tagged (#153 to #158).

## Reviews of 12 October, and their fixes: done
- Two internal reviews, of the tagging process (#187) and of security (#188).
- **Hostile files** (#189 to #207): a form that draws itself, a looping page tree, CID font widths, absurd page
  sizes, deep or broken tag trees, bad marked-content ids and table spans no longer hang or fail a job.
- **Speed on large files** (#195 to #215): pictures for the AI rendered right before their call, the original's tag
  map built while the validator and AI are waited for, and layout comparisons limited to neighbours.
- **Security fixes** (#201 to #228): LIKE wildcards in audit filters taken literally; bounds on a save's edits,
  on request size and on the file's title and language; an engine page cap (`MAX_PAGES`); document text sent to
  the AI as data, with control characters stripped; network policies, a Redis password and one secret per pod;
  pods bounded (seccomp, no capabilities, read-only root filesystems); every image pinned to a digest; a
  dependency audit whenever dependencies change.

## Portal, 13 October: done
- An AI level can be deleted when it's off and unused; its models are chosen from a list (#231).
- Gateway model names say family, size and role (#234); a small and a large commercial writer added (#230).
- A group's documents listed a page at a time, with search, filters and sorting (#235).
- **Live queues** (#236): every queue's items in one list, with search and filters, updated every three seconds.
- Three built-in caps became settings, for documents towards a thousand pages: `AI_TABLES_MAX`,
  `AI_ORDER_PAGES_MAX` and `PORTAL_REVIEW_FINDINGS_MAX` (#237).

## Engine: notation, structure and footnotes, 12 to 15 October: done
- **Notation:** doses put in words by rule with no AI call; one chemistry style; only the formula inside a
  sentence goes to the AI; what will be spoken checked against the page (#179 to #186). A drawn chemical structure
  or reaction scheme is one figure with its atom labels (#238 to #244). Formulas on scanned pages found and read
  (#183). On the Adobe route, notation readings go onto Adobe's tags (#20, part 3).
- **Bookmarks** from the headings (#277). **Complex tables** link each cell to its headers (#278).
- **Footnotes** tagged as notes and linked from their marks (#279), then marks said as footnotes, made
  References of their own, linkable by hand, linked from table cells, and kept on the kept-tags route (#285).
- **OCR:** a scanned page with values typed onto it is read (#292).
- **Tag text** gets a space where a line ends (#298); a heading comes before the text under it, whatever the AI
  says (#297).
- CI's actions run on Node.js 24 (#169).

## Long documents in production, 15 and 16 October: done
- A document no longer gets a second copy or endless reruns, and veraPDF can't take the worker down (#308).
- The checker keeps only what it reads, and worker processes don't carry a long document over (#309).
- Both veraPDF profiles are checked side by side through the service (#310).
- A drawn fraction bar counts as a division (#311).
- A lost worker is reported to the portal, with when it's tried again (#315).
- A NUL in the engine's answer no longer stops the portal worker (#324); the engine strips control characters
  from kept tags' text (#325).

## Super admins: cost, limits and re-runs (#312 to #327): done
- **Dollar costs** and the engine's own JSON report are for super admins only (#314).
- **Limits hit** are recorded with each document and reported (#312). A **Run details** tab for super admins: AI
  cost, calls and models, pages and limits (#313). Describe with AI's use and cost logged per use (#318).
- **Re-run** any document as a new one, charging the owner only when asked, and **Restart** a stuck item from
  Queues (#323, migration 0038).
- **Security tests:** attack strings through every search, sort and filter, with Bandit's SQL check in CI
  (#326); a document whose text carries SQL, HTML, a formula, a NUL and an AI instruction (#327).
- An internal feature assessment of 16 October (#332).

## Operations and hardening, 17 October: done
- **Liveness probes** (#335): a stuck engine worker, portal worker or API is restarted. The APIs answer
  `GET /api/v1/live`.
- **Alerts** (#334, migration 0039): the queue not moving, the portal worker stopping, or the engine without
  workers is emailed to super admins, with reminders and a cleared note, and shown on Attention.
- **Nightly backups** (#333, #346): `BACKUPS=cronjob` runs a CronJob onto an NFS share, with pruning and an alert.
- **Proxy headers** count only from the proxies in front of the portal (`PORTAL_TRUSTED_PROXIES`, #336).
- **Database account** (#337): the portal connects as `portal_app`, which owns no table. Row-level security is
  #350.
- **Uploads:** pages counted in a child process with memory, CPU and time limits (#356).
- **Rate limits per person** on downloads, exports and uploads (#357).
- **Egress** (#361): the engine reaches the internet only through the AI gateway.
- **Dependencies** (#362): Dependabot weekly per ecosystem, a weekly audit, and Trivy in `scripts/build.sh`;
  the libraries Trivy flagged in the music and web images replaced (#393).

## People and sign-in, 17 October: done
- **A person's data** (#338, migration 0040): super admins export it, or remove it on request; the records stay
  with the name and email replaced.
- **Sign out of all devices** (#354): a button in Settings, and for super admins on anyone.
- **Sign-in** gives the same answer for every address the service would take, at the same speed (#359).
- **Low pools:** group admins emailed once when their pool runs low (#358).
- **Accessibility of the app:** keyboard focus shown on the upload buttons (#353); `scripts/a11y.sh` runs axe over
  the main pages, locally and in CI on demand (#369).

## Engine: documents of every kind, 17 October: done
- **Signed and permission-protected PDFs** (#339): signed ones held for the owner's confirmation; restricted ones
  keep their restrictions, with access for assistive technology.
- **Language** detected from the text, a template's wrong one replaced, passages in another language tagged
  (#340).
- **Form fields** named from their printed labels, with the AI for the rest (#341).
- **XFA forms** (#360): a dynamic one refused at upload, a static one's XFA part removed.
- **Low contrast** text listed per page for review (#367).
- **Right-to-left pages** (Arabic, Hebrew) read from the right, and listed to check (#368).
- `PDFUA_PART` other than 1 is refused (#355); PDF 2.0 files were handled later (#409).

## Tag manager, 17 and 18 October: done
- The document's title and language edited in a Document panel (#363).
- Link descriptions, and any text or figure tag marked as an artifact (header, footer, page number) (#364).
- Undo and redo of the unsaved changes, with Ctrl+Z and Ctrl+Shift+Z (#365).
- **Review items signed off** (#366, `finding_marks`, migration 0041): checked, fixed or not an issue, kept with
  the same item in later versions, with a filter and a CSV.
- **Tables:** a table editor, text tags made into a table (#370), a figure that is a drawn table made a table
  (#395), and a pending table edit shown as it will be saved (#400).
- **Ignore the existing tags** at upload, to tag a document afresh (#396, migration 0042).

## Monitoring (#397, #398): done
- `MONITORING=on` (the default) deploys Prometheus, Grafana at `/grafana` for super admins (signed in through the
  portal), kube-state-metrics and the Redis and Postgres exporters. The portal, its worker and the engine serve
  metrics.
- Prometheus's alert rules (`deploy/k8s/monitoring/rules.yml`, with tests) reach super admins like the portal's
  own alerts.
- **Administration › System:** figures and charts over 6 hours, a day or a week, and every workload's pods with
  their state and details, all from Prometheus, so the portal needs no access to the Kubernetes API.
- Details in [monitoring](../operations/monitoring.md).

## Math as MathML (#403): done
- **Part 1:** the LaTeX that comes with the AI's reading of a formula is converted to MathML and checked. A
  document with formulas gets a formula list (a web page) and a zip of MathML files.
- **Part 2:** MathCAT, built into the engine image, reads each formula by rule (ClearSpeak speech, Nemeth and UEB
  braille). Where its speech and the AI's reading disagree, the formula is flagged and the tag manager offers both.
  Two more downloads: the formulas in Nemeth and in UEB braille.
- **Part 3:** a document's own MathML (from LaTeX's tagging and other PDF 2.0 producers) is kept, read by rule and
  joins the math downloads.
- **Part 4:** in a PDF 2.0 file, each formula's MathML is attached to its tag as an associated file.
- **Part 5:** an optional formula reader (`FORMULA_READER=on`, RapidLaTeXOCR, on demand like the music reader)
  reads each formula from its picture; a difference from the AI's reading flags it.

## PDF/UA-2 for PDF 2.0 documents (#409): done
- A PDF 2.0 file can only be PDF/UA-2. Its verdict comes from veraPDF's UA-2 profile (UA-1 for the rest), and its
  output is labelled PDF/UA-2 with the root tag in the PDF 2.0 namespace. PDF 1.x is unchanged.
- The portal keeps the part, and the badge, verdicts, reports, email and file names follow it.

## Large documents on more than one core (#124): done
- **Part 1:** a document of 50 pages or more has its layout analysis split over `PAGE_WORKERS` processes (3 by
  default); the pages are put back in order, so the result is the one a single process makes.
- **Part 2:** the checks' reading of each page is shared out the same way.
- If a process fails, the work is done in one process as before.
