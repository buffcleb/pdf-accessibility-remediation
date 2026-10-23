# API reference

> **Changed 22 October 2026:** the engine's API key is required (#457), `GET /api/v1/capacity` counts a stopping
> worker's document (#531), and the portal's API for scripts has [a section of its own](#the-portals-api-for-scripts).

The portal is the normal way in. The engine's API is **internal**, for the portal, integrations and testing; never
expose it to the internet. Interactive documentation is served at `/docs` on the engine (for example
`http://localhost:8000/docs`).

Every call except `/health`, `/live` and `/metrics` needs an `X-API-Key` header with `SERVICE_API_KEY` (`401`
without it). The API won't start without that setting, unless `ALLOW_NO_API_KEY=true` (local development only: the
API then takes calls without a key).

## Contents

- [Remediate a document](#remediate-a-document)
- [Job status and results](#job-status-and-results)
- [Reports](#reports)
- [Apply tag edits](#apply-tag-edits)
- [Classify a document](#classify-a-document)
- [Other endpoints](#other-endpoints)
- [Many documents: the harness](#many-documents-the-harness)
- [The portal's API for scripts](#the-portals-api-for-scripts)

## Remediate a document

`POST /api/v1/remediate` (multipart)

| Field | |
|---|---|
| `file` | The PDF (at most `MAX_UPLOAD_MB` and `MAX_PAGES` pages; password-protected files are refused) |
| `ai_provider` | `gateway` (the AI gateway; normal), `none` (no AI; default), `mock` (tests only; `ALLOW_MOCK_PROVIDER`), or for development `localai`, `ollama`, `openrouter` |
| `ai_model` | The gateway model, e.g. `local-vision`; defaults to `GATEWAY_DEFAULT_MODEL` |
| `ai_tier` | Optional: the portal's AI level, attached to each AI call for spend reporting |
| `judge_model` | Optional: the review model at the gateway; defaults to the engine's |
| `route` | `standard` (default) or `adobe` (Adobe Autotag; refused unless `ADOBE_ENABLED=true`, and not with `report_only`) |
| `flags` | JSON options (below) |

**Flags** (`-F 'flags={"language":"en-CA","full_review":true}'`):

| Flag | Default | Effect |
|---|---|---|
| `language` | detected, or `en-US` | Document language (BCP 47), e.g. `fr-CA` |
| `title` | from the document | Override the title |
| `report_only` | `false` | Check only: report problems, change nothing |
| `full_review` | `false` | The AI reviews every figure and formula, not just flagged ones plus a 10% sample |
| `verify_ai` | `true` | Run the AI review layer |
| `notation_ai` | `true` | Let the AI read notation the layout rules can't |
| `ocr_ai` | `true` | Let the AI correct OCR words Tesseract was unsure of |
| `ignore_existing` | `false` | Ignore the document's own tags (poor ones) and tag it afresh from the page |
| `validator` | the service when configured | How veraPDF runs for this job: `service` (kept running, `VERAPDF_URL`) or `command_line` |
| `verapdf_ua2` | `true` | Also check PDF/UA-2 (shown for information; the verdict is PDF/UA-1, except for a PDF 2.0 file, judged by PDF/UA-2; each snapshot's `pdfua_part` says which) |
| `return_base64` | `false` | Return files inline in the JSON instead of as links |
| `report_branding` | none | For the PDF report: `service_name`, `organization`, `accent_color`, `ai_label` |
| `debug` | `false` | Keep working files and AI prompt and response logs (the document's text with them, on the job folder until `JOB_TTL_HOURS`) |

Returns `202 {"job_id": "…", "status": "processing"}`. A file that isn't a PDF is refused with `415`, one over
`MAX_UPLOAD_MB` with `413`; an empty, unreadable or password-protected file, too many pages, bad flags or an unknown
provider with `422`.

```bash
curl -F "file=@report.pdf" -F "ai_provider=gateway" -F "ai_model=local-vision" http://localhost:8000/api/v1/remediate
```

## Job status and results

`GET /api/v1/jobs/{job_id}`

While the job runs, `status` is `processing`, with a `progress` stage. An edit job whose file and tag map are
already final, and only its checks are left, also gives `partial`: `{"files": {"remediated_primary": url},
"tag_map": url}`, so the version can be opened before it's checked. A job whose worker was killed before it could
record a failure (the hard time limit, out of memory) is reported as `failed` with error `time_limit_exceeded` or
`worker_lost`, not left as `processing`. When it finishes:

```jsonc
{
  "status": "completed",
  "files": {
    "remediated_primary": "/api/v1/jobs/{id}/files/x_remediated.pdf",   // or base64 with return_base64
    "remediated_visual_fallback": null,                                 // set only when there are two files
    "enhanced": null                       // Adobe route with AI: Adobe's file plus AI alt text
  },
  "metrics": { "pages_processed": 14, "ai_tokens_used": {"prompt_tokens": 14500, "completion_tokens": 2300},
               "processing_time_seconds": 45.2, "ai_calls": 31, "images_described": 9, "ai_cost_usd": 0.0,
               "stage_seconds": {"validating_original": 3.1, "analyzing": 6.0, "ai_enrichment": 18.4, "tagging": 2.2,
                                 "validating_output": 4.9, "verifying_tags": 9.8, "of_which": {"validator": 7.2}}, ... },
  "reports": {
    "verapdf_compliance_report": "...after (primary) XML...",
    "verapdf_before": "...XML for the original...",
    "verapdf_after": {"primary": "...", "visual_fallback": null},
    "comparison": {
      "before": {"compliant": false, "failed_rules": 10, "failed_checks": 66, "tagged": false, ...},
      "after":  {"compliant": true,  "failed_rules": 0,  "failed_checks": 0,  "tagged": true,  "pdfua_part": 1, ...},
      "delta":  {"failed_checks": {"before": 66, "after": 0, "change": -66}, ...},
      "existing_tags": {"retained": 1, "repaired": 3, "replaced": 1, "rebuilt_tree": false, ...},
      "visual_fidelity": {"pages_identical": 2, "pages_compared": 2, "identical": true}
    },
    "tag_verification": { ... },             // the three verification layers' findings
    "tag_maps": {"original": "...", "primary": "...", "visual_fallback": null},   // for the tag manager
    "remediation_summary": "Before: FAILS ... After: PASSES ...",
    "ai_findings": ["page 3: alt text generation failed (...)"],
    "markdown_report": "/api/v1/jobs/{id}/files/x_remediation_report.md",
    "report_pdf": "/api/v1/jobs/{id}/files/x_remediation_report.pdf",
    "full_report": "...JSON with per-element tagging detail..."
    // and, when they apply: music_files, music_score, math_list, math_files, math_nemeth, math_ueb (below),
    // edits_applied (edit jobs), adobe_reports (Adobe's own reports, xlsx, Adobe route only)
  },
  "warnings": []
}
```

A failed job has `status: failed` with an `error` code and message, for example `ai_provider_unavailable` or
`tag_integrity_failed`.

`GET /api/v1/jobs/{job_id}/files/{name}` serves the files. Job folders are removed after `JOB_TTL_HOURS`.

## Reports

- **Markdown:** `GET /api/v1/jobs/{id}?format=markdown` (or `md`) returns the result as a readable report: the
  verdict, file links, a before-and-after table, the remaining veraPDF failures with their requirement text,
  decisions on existing tags, AI findings and metrics. It also works while a job is processing or after it failed.
  Each completed job also saves `*_remediation_report.md`.
- **PDF:** each completed job saves the report as an accessible PDF (PDF/UA-1), `reports.report_pdf`. Branding (the
  service and organization names, an accent color, the AI level's name) comes from the `report_branding` flag.
- **Music and math:** a document with music the reading service read has `reports.music_files` (a zip of
  MusicXML) and, for sheet music, `reports.music_score`. One with formulas the AI read has `reports.math_list` (an
  HTML page with every formula in MathML) and `reports.math_files` (a zip with one `.mml` per formula); the full JSON
  report lists them under `math`, each with its page, region, spoken reading, LaTeX and MathML (#403), and where
  MathCAT read them, its reading and braille (`rule`). `reports.math_nemeth` and `reports.math_ueb` are every
  formula in braille (BRF). The full JSON report's `math_disagreements` lists the formulas whose AI reading and rule
  reading say different numbers or letters. An edit job records which reading people chose (`math_choices`, from
  `reading` on a `set_alt` or `set_actual_text` edit) in its result on the job folder; neither is in the job
  response.

## Apply tag edits

`POST /api/v1/edit` (multipart): applies the tag manager's edits to a remediated PDF and checks the result.

| Field | |
|---|---|
| `file` | The PDF to edit (a remediated output) |
| `edits` | JSON list of edit operations (role changes, alt text, read-aloud text, language, cells, reading order, merge, split, delete, moving content between tags, tagging artifacts) |
| `review` | `none` (default), `sampled` (flagged items plus a sample) or `full` (every figure). With no edits, a review re-reviews the file as it is |
| `verify_ai` | `true` (default): run the AI review layer when checking the result |
| `judge_model` | Optional review model |
| `reference` | Optional: the version a corrected file replaces; the visual comparison is made against it |
| `validator`, `verapdf_ua2` | Optional: how veraPDF runs, as in the remediation flags |
| `ai_read` | Optional JSON list: the AI's readings flagged on the version this one is based on, each `{"ai_read": [page, bbox, kind, spoken], "element": n, "readings": {...}}` as that version's findings carry them in `extra`. They're flagged again on the result, except where these edits give the element new text |

Returns a job id, polled like a remediation.

## Classify a document

`POST /api/v1/classify` (multipart): which of an AI level's kinds of document a PDF is, from its first pages' text
(no images). Answers at once; nothing is kept.

| Field | |
|---|---|
| `file` | The PDF |
| `kinds` | JSON list of `{key, label, description}`; "general" is always offered |
| `backend` | `jev` (TypeSafe Jev through OpenRouter's System One API; needs `OPENROUTER_API_KEY`) or `gateway` (a gateway model) |
| `model` | The gateway model (for `gateway`), or a Jev model name |
| `pages` | Pages to read (default 3): the first ones, plus pages spread through long documents |

Returns `{kind, confidence, probabilities, backend, model, notation, hints, pages_read, seconds, …}`. `kind` is
`null` when there's no text to go on (`reason: no_text`) or the answer wasn't one of the kinds.

## Other endpoints

- `GET /api/v1/ai/models`: the models the gateway serves (`503` when the gateway can't be reached).
- `POST /api/v1/describe` (multipart: `file`, `page`, `bbox` as JSON `[x0, y0, x1, y1]`, `ai_provider` (default
  `gateway`), `ai_model`, `long`): alt text from the AI for an area of a page. Answers at once with `{alt, kind,
  decorative, latex, model, cost_usd, prompt_tokens, completion_tokens}`; nothing is kept.
- `GET /api/v1/live`: for the liveness probe; answers `{"status": "alive"}` without checking anything else.
- `GET /api/v1/health`: `status` (`ok` or `degraded`), Redis, veraPDF (the command line, and `verapdf_service`
  when one is configured), the AI providers and Adobe's status.
- `POST /api/v1/jobs/{job_id}/stop`: stops a job that's waiting or in progress. It reports as failed with error
  `stopped`, and the worker process running it is ended. A finished job is left as it is (`"stopped": false`).
- `GET /api/v1/capacity`: `{"workers": 2, "documents_at_once": 4, "documents_on_stopping_workers": 0}`, the
  workers answering now and how many documents they process at once between them (`null` when none answer). The
  portal uses it to decide how many documents to hand over when workers are scaled with the queue. A worker told to
  stop (a new release, scaling down) finishes its document but no longer answers; that document still counts in
  `documents_at_once` while its heartbeat is fresh, and `documents_on_stopping_workers` says how many there are
  (#531), so new documents go to the new worker meanwhile.
- `GET /api/v1/metrics`: for Prometheus (#397), in its text format, without the API key: what's waiting in Redis
  (`engine_queue_length` by queue: `engine`, `adobe`, `music`) and `engine_redis_up`. Each worker serves its own on
  port 9464 (`WORKER_METRICS_PORT`; `WORKER_METRICS=off` turns it off): `engine_worker_busy` and
  `engine_worker_places`. On the cluster only Prometheus can reach either
  ([monitoring](../operations/monitoring.md#prometheus-and-grafana)).

The engine has no re-run call: a re-run in the portal (`POST /api/v1/jobs/{id}/rerun` there, super admins) sends
the original file to `/remediate` again as a new job, with the same options (and `ignore_existing` when asked for).

## Many documents: the harness

```bash
python harness/run_harness.py path/to/folder --provider gateway --model local-vision --markdown --out harness_output/my_run
```

See [harness/README.md](../../harness/README.md).

## The portal's API for scripts

> **New 22 October 2026:** personal API tokens (#518).

People can send their own documents from a script with a **personal API token**, made in the portal's Settings
once super admins and their group's admins have switched tokens on. This is the portal's API, at the service's
public address, not the engine's. The guide for people writing the scripts, with `curl` examples, is
[Scripting](../user-guide/people/scripting.md); what super admins set is in
[Settings](../user-guide/super-admins/settings.md).

- **Sign-in:** `Authorization: Bearer pdfr_…`. The token is shown once; only a keyed hash of it is kept (under
  `PORTAL_SECRET_KEY`, so a new one of those stops every token).
- **What a token may call:** only its owner's own documents, on the document routes:

  | Call | Needs |
  |---|---|
  | `POST /api/v1/jobs` (multipart, one or more `files`) | Always allowed. Each document uses the token's processing service, AI level and kind, whatever the request says |
  | `GET /api/v1/jobs/{id}`, `GET /api/v1/documents` (`page`, `page_size` up to 200, the list's filters) | **Check their status** |
  | `GET /api/v1/documents?not_downloaded=true` | Download: the finished documents this token hasn't downloaded yet |
  | `GET /api/v1/jobs/{id}/files.zip`, `GET /api/v1/jobs/{id}/files/{file}` | Download: **Everything**, or **Chosen files** one at a time |

  Anything else, Administration and Settings included, is refused with `403 token_not_allowed`, whoever owns the
  token; a call the token wasn't given is `403 token_scope`.
- **Limits:** five tokens a person, 90 days by default and 365 at most, stopped after 90 days unused (Settings ›
  API tokens for scripts); the same per-person hourly limits as the web app (`429` past one). Switching tokens off,
  for the service or a group, stops every one at once. ([Limits](../operations/limits.md#reports-files-and-sign-in))
- **Records:** documents sent this way are marked **sent by script** with the token's name; making and revoking a
  token is in the audit log (`api_token.created`, `api_token.revoked`). Owners are emailed before a token expires.

The rest of the portal's API is for its own web app and may change between releases. With `PORTAL_ENABLE_DOCS=true`
its interactive documentation is at `/api/v1/docs`; leave it off in production.
