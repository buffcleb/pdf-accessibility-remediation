# Source map

[Architecture](README.md) › Source map

> **Changed 22 October 2026:** the files added since 19 October: API tokens, malware scanning, row-level security,
> security alerts and the SIEM export, paged lists and zips in the portal; the link and structure repairs in the
> engine; the newer manifests and scripts.

```
app/                         the remediation engine
  main.py, config.py         the FastAPI app; every setting (environment variables)
  metrics.py                 the engine's Prometheus metrics (queue lengths; each worker's documents at once)
  storage.py                 the shared job folder (one directory per job)
  classify.py                the kind-of-document classifier (text sample, Jev or a gateway model)
  api/          routes.py (remediate, edit, classify, describe, jobs, stop, files, ai/models, capacity, metrics,
                live, health), schemas.py
  worker/       celery_app.py, tasks.py (remediate; remediate_adobe on the "adobe" queue; edit_tags; purge_expired)
  pipeline/     orchestrator.py   the standard route, step by step
                analyzer.py       layout: text runs, scripts, fractions, tables, images, columns
                structure.py      roles, headings, captions, reading order;  lists.py  list nesting and order
                enrichment.py     AI calls: alt text, tables, reading order, notation
                notation.py       notation the rules can't read; checking the AI's reading against the page
                mathml.py         formulas as MathML (from the AI's LaTeX, or the document's own); math downloads
                mathspeak.py      MathCAT's speech and Nemeth and UEB braille, checked against the AI's reading
                formula_ocr.py    the optional formula reader (docker/formula), asked over Redis
                language.py       the document's language from its text; passages in another language
                field_labels.py   form fields named from their printed labels (the AI for the rest)
                protection.py     signed and permission-protected PDFs
                page_pool.py      a large document's per-page work shared out over processes
                limits.py         the limits a document reached, for its metrics and reports
                ocr.py, ocr_ai.py OCR lines with confidence; AI correction under checks
                scan_figures.py   figures inside scanned pages (clipped redraws of the scan)
                transfer.py       carrying AI results over to a re-analyzed (flattened) document
                music.py, omr.py  music examples as figures; the music reading service, asked over Redis
                plots.py          graphs drawn as hundreds of specks or one long path, as one figure each
                schemes.py        chemical structures and reaction schemes drawn with lines, as one figure each
                edit_job.py       saves from the tag editor;  adobe_job.py  the Adobe route
                fonts.py, models.py
  tagging/      content_marker.py marked-content rewriter (text-run splitting, clips)
                tagger.py, struct_tree.py, targets.py   the structure tree, and what each piece of content belongs to
                existing_tags.py  auditing, repairing and rebuilding existing tags
                link_words.py, self_links.py   kept Links given their words; links that point to themselves removed
                structure_rules.py   tags at the right level, and figures' placement and boxes, as PAC checks
                mathml_tags.py, mathml_embed.py   a document's own MathML kept; MathML put on Formula tags (PDF 2.0)
                table_headers.py, bookmarks.py   cells linked to their headers; bookmarks from the headings
                editor.py, role_rules.py   tag edits, and which role changes are valid
                placement.py, reorder.py   where added tags go among kept ones; kept tags put in reading order
                empty_spans.py, legend.py  Spans around nothing, folded away; marks a page's legend explains
                form_marks.py, blank_fonts.py   check marks redrawn as shapes; fonts that draw nothing
                font_*.py, metadata.py, destructive.py (flattening)
  ai/           base.py (provider contract), gateway.py (LiteLLM), openai_compat.py, localai.py, ollama.py,
                openrouter.py, mock.py, prompts.py, registry.py
  verification/ integrity.py, semantic.py, ai_review.py, verify.py, tagmap.py (for the viewer), model.py,
                contrast.py (text with low contrast)
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
  attention.py, audit.py     what needs admins' attention (and what was acknowledged); the audit log
  security_alerts.py, siem.py   security alerts from the audit log; the audit log sent to a SIEM
  rls.py                     row-level security: whose request each transaction is
  api_tokens.py              personal API tokens for scripts
  malware.py                 uploads streamed to ClamAV before they're stored
  export.py, paging.py       zips of many documents, made as they're sent; one page of a list
  alerts.py, pool_alerts.py  the service's alerts to super admins; a group's pool running low
  metrics.py, prometheus.py, workloads.py   the portal's Prometheus metrics; reading Prometheus; the System page's pods
  workload_catalog.py        what each workload is and where it comes from (the pods' details)
  privacy.py, removal.py     a person's data exported or removed; documents' files removed early, documents deleted
  finding_marks.py, tagmarks.py, ai_describe.py   review items signed off; original tags; who may Describe with AI
  pdfcheck.py, storage.py    upload checks; the object store
  worker.py, engine.py       job, version and review processing; the engine client
  settings_store.py          runtime settings (changed in the app)
  cli.py, backup_tools.py    operator commands; file export and import for backups
  routers/      auth, me, branding, jobs, versions, help, groups, admin, reports, queues, email_reports, monitoring,
                api_tokens, audit_export (the SIEM's pull API)
  security/     tokens.py (JWT, codes), ratelimit.py, botcheck.py (Cloudflare Turnstile)

web/src/                     the React app
  pages/        Home (My documents), JobDetail (the document page), Login, MySettings, Unsubscribe
  pages/admin/  index (menu, Attention), Groups, People, Services, Setup (guided set-up), Help, Queues,
                QueuesLive, Settings, System and Pods (monitoring), shared
  components/   UploadForm, DocumentsList, Allowances, LiveReport, ReportChart, ApiTokens, AdminDialog (the
                standard edit dialog), lists (the shared pager and search bar), RunDetails, help, …
  viewer/       TagViewer, Pane, PageView, TreePane, EditPanel, Reader (read aloud), draft.ts, roles.ts,
                speech.ts (what read aloud says), pdf.ts, DocumentPanel (title and language), TableDialog and
                tablegrid.ts (the table editor), history.ts (undo and redo), ReadingPicker (a formula's readings),
                ListBuilder, FigureSplit, TagMenu and the other dialogs
web/tests/                   unit tests of the viewer's logic (npm test: tsc, then Node's test runner)

config/litellm.yaml          the AI gateway's models
docker/                      the music reader (audiveris), the formula reader (formula), mathcat-cli (mathcat),
                             the web image's nginx (web)
deploy/k8s/                  Kubernetes manifests and settings templates (23-redis-portal: the portal's own Redis;
                             26-egress-proxy and 82-egress-enforce: outbound traffic; 27-clamav: malware scanning;
                             35-verapdf: the veraPDF service and its hourly restart; 37-formula: the formula reader;
                             45-autoscale: scaling with the queue; 62-traefik-api-origin: the API origin on Traefik;
                             80-network-policies; 90-backup: the nightly backup; 95-monitoring and monitoring/:
                             Prometheus and Grafana), egress_hosts.py (the proxy's allow-list), gateway_key.py (the
                             engine's own gateway key)
deploy/node/                 rke2-drain: drain a node before it shuts down
deploy/verapdf/              settings of the veraPDF service (how many failed checks it reports, with messages)
deploy/cloudflare-worker/    the optional Cloudflare Worker front end
scripts/                     build.sh (and its Trivy scan), deploy.sh, deploy-worker.sh, backup.sh, restore.sh,
                             rotate-secret.sh, check_cluster_encryption.sh (read-only), sign-in-code.sh,
                             audit-deps.sh (pip-audit and npm audit, for CI and the weekly audit),
                             regression.sh (the regression documents), a11y.sh (axe over the web app), publish.sh
harness/                     make_samples.py (synthetic PDFs), run_harness.py (batch tester), run_folder.py, ocr_eval.py
tests/                       engine/ and portal/ test suites; a11y/ (the web app's accessibility checks)
```
