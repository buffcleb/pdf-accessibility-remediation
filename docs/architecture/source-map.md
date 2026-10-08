# Source map

[Architecture](README.md) › Source map

```
app/                         the remediation engine
  main.py, config.py         the FastAPI app; every setting (environment variables)
  storage.py                 the shared job folder (one directory per job)
  classify.py                the kind-of-document classifier (text sample, Jev or a gateway model)
  api/          routes.py (remediate, edit, classify, jobs, files, ai/models, health), schemas.py
  worker/       celery_app.py, tasks.py (remediate; remediate_adobe on the "adobe" queue; edit_tags)
  pipeline/     orchestrator.py   the standard route, step by step
                analyzer.py       layout: text runs, scripts, fractions, tables, images, columns
                structure.py      roles, headings, captions, reading order;  lists.py  list nesting and order
                enrichment.py     AI calls: alt text, tables, reading order, notation
                notation.py       notation the rules can't read; checking the AI's reading against the page
                ocr.py, ocr_ai.py OCR lines with confidence; AI correction under checks
                scan_figures.py   figures inside scanned pages (clipped redraws of the scan)
                transfer.py       carrying AI results over to a re-analyzed (flattened) document
                edit_job.py       saves from the tag editor;  adobe_job.py  the Adobe route
                fonts.py, models.py
  tagging/      content_marker.py marked-content rewriter (text-run splitting, clips)
                tagger.py, struct_tree.py, targets.py   the structure tree, and what each piece of content belongs to
                existing_tags.py  auditing, repairing and rebuilding existing tags
                editor.py, role_rules.py   tag edits, and which role changes are valid
                font_*.py, metadata.py, destructive.py (flattening)
  ai/           base.py (provider contract), gateway.py (LiteLLM), openai_compat.py, localai.py, ollama.py,
                openrouter.py, mock.py, prompts.py, registry.py
  verification/ integrity.py, semantic.py, ai_review.py, verify.py, tagmap.py (for the viewer), model.py
  validation/   verapdf.py, audit.py
  reports/      markdown.py, pdf.py (the accessible PDF report)
  adobe/        client.py (Adobe REST, retries), limiter.py (shared rate limits), chunks.py (split and merge),
                fake.py (a stand-in Adobe server for tests and local runs)

portal/                      the portal API and worker
  main.py, config.py, db.py, deps.py, errors.py
  models.py, migrations/     tables and Alembic migrations
  identity.py, approval.py   who may sign in and which group they join; member approval
  services.py, quota.py      who can use what; page accounting per service (the ledger)
  queues.py, outbox.py       queue order, pause and resume, retry and cancel; the email outbox
  helpdesk.py                help requests: access, attachments, status, reminders
  live_reports.py, report_views.py   the live reports; scheduled report views
  email_reports.py, notifications.py, mail.py   scheduled summaries; notification preferences; mail providers
  attention.py, audit.py     what needs admins' attention; the audit log
  pdfcheck.py, storage.py    upload checks; the object store
  worker.py, engine.py       job, version and review processing; the engine client
  settings_store.py          runtime settings (changed in the app)
  cli.py, backup_tools.py    operator commands; file export and import for backups
  routers/      auth, me, branding, jobs, versions, help, groups, admin, reports, queues, email_reports
  security/     tokens.py (JWT, codes), ratelimit.py

web/src/                     the React app
  pages/        Home (My documents), JobDetail (the document page), Login, MySettings, Unsubscribe
  pages/admin/  index (menu, Attention), Groups, People, Services, Setup (guided set-up), Help, Queues,
                Settings, shared
  components/   UploadForm, DocumentsList, Allowances, LiveReport, ReportChart, help, …
  viewer/       TagViewer, Pane, PageView, TreePane, EditPanel, draft.ts, roles.ts, pdf.ts

config/litellm.yaml          the AI gateway's models
deploy/k8s/                  Kubernetes manifests and settings templates
deploy/cloudflare-worker/    the optional Cloudflare Worker front end
scripts/                     build.sh, deploy.sh, deploy-worker.sh, backup.sh, restore.sh, sign-in-code.sh
harness/                     make_samples.py (synthetic PDFs), run_harness.py (batch tester), ocr_eval.py
tests/                       engine/ and portal/ test suites
```
