# Limits

[Operations](README.md) › Limits

Every limit on what one document, one person or one request can do: its default, where to change it, and what
happens past it. A limit that cuts a result short says so in the document's report (under **Limits reached**, and
in **Findings and warnings**), so a capped result isn't mistaken for a complete one. Each one is also recorded with
the document (`metrics.limits_hit`: the setting, its value, what the document wanted), and Administration › Reports ›
**Limits hit** counts them across documents, so you can tell when a limit needs raising.

**Where to change**
- **Settings:** Administration › Settings in the portal (super admins). Takes effect at once.
- **Engine:** an environment variable of the engine: `.env` for local Docker, `deploy/k8s/engine.env` on a cluster
  (then deploy).
- **Portal:** an environment variable of the portal: `.env.portal` for local Docker, `deploy/k8s/portal.env` on a
  cluster (then deploy).
- **Deploy:** `deploy/k8s/deploy.env` (then deploy).
- **Built in:** a constant in the code, named here with its file. Changing it means a new build.

## Contents

- [Long documents: what to raise together](#long-documents-what-to-raise-together)
- [Uploading](#uploading)
- [Queues and time](#queues-and-time)
- [What the AI is asked](#what-the-ai-is-asked)
- [Music](#music)
- [Formula reader](#formula-reader)
- [Checking the result](#checking-the-result)
- [Adobe Autotag](#adobe-autotag)
- [Tag manager and editing](#tag-manager-and-editing)
- [Help requests](#help-requests)
- [Reports, files and sign-in](#reports-files-and-sign-in)

## Long documents: what to raise together

The defaults suit documents of up to a few hundred pages on a small AI server. For documents towards 1,000 pages
on a hosted model, these go up together; raising one alone moves the cut to the next:

| Setting | Default | For about 1,000 pages | Why |
|---|---|---|---|
| `TASK_TIME_LIMIT_S` | 3600 | 14400 | More AI work takes longer than an hour |
| `AI_CONCURRENCY` | 4 | 8 | Four suits one GPU; a hosted model takes more at once |
| `VERIFY_MAX_PAGES` | 400 | 1200 | Later pages otherwise get the structure checks only (and no contrast check) |
| `VERIFY_AI_MAX_ITEMS` | 150 | 1000 | What the reviewer looks at, per file |
| `PORTAL_REVIEW_FINDINGS_MAX` | 300 | 1000 | Or part of what was reviewed isn't listed in the app |
| `AI_NOTATION_MAX` | 100 | 1000 | A math or chemistry text passes 100 in a few chapters |
| `AI_TABLES_MAX` | 100 | 500 | |
| `AI_ORDER_PAGES_MAX` | 200 | 1000 | |
| `OCR_AI_MAX_LINES` | 300 | 3000 | A long scan uses 300 within a few pages |

Every one of these is more AI calls, so more time and, on a paid model, more money: check the gateway's
`max_budget` (`config/litellm.yaml`, USD per 30 days across all models) and the AI level's page limits against
what the documents will cost. A worker pod's memory (`WORKER_MEMORY_LIMIT` in `deploy.env`, shared by the
documents it processes at once) is worth watching on the first long document. These figures are sized by
reasoning, not measured on a 1,000-page document.

## Uploading

| Limit | Default | Change | Past it |
|---|---|---|---|
| Size of one file | 100 MB | Settings: Each upload | The upload is refused |
| Pages in one document | 2,000 | Settings: Each upload (a service can set less) | The upload is refused |
| Files in one upload | 20 | Settings: Each upload | The upload is refused |
| Size the engine accepts | 200 MB | Engine: `MAX_UPLOAD_MB` | The engine refuses the file; keep it at or above the portal's |
| Pages the engine accepts | 2,000 | Engine: `MAX_PAGES` (0: no limit) | The engine refuses the document; keep it at or above the portal's, so the portal's own message is the one people see |
| Pages a person or group may process | Their allowance | Groups and allowances | The document waits, or is refused, as the group is set up |
| Opening a file to count its pages | 1,024 MB, 30 s of CPU, 60 s in all | Portal: `PORTAL_PDFCHECK_MEMORY_MB`, `PORTAL_PDFCHECK_CPU_S`, `PORTAL_PDFCHECK_TIMEOUT_S` | The upload is refused as too large or complex to open safely (it's opened in a child process, so the API isn't affected) |
| Size of an upload through the cluster's web app and Ingress | 100 MB | Deploy: `MAX_UPLOAD_SIZE` | The upload is refused; keep it at or below Cloudflare's limit when behind Cloudflare |
| Upload requests by one person | 200 an hour | Portal: `PORTAL_RATE_UPLOADS_PER_HOUR` (0: off) | A plain 429; it clears within the hour |

## Queues and time

| Limit | Default | Change | Past it |
|---|---|---|---|
| One person's documents in progress | 5 | Settings: Each person's queue | The rest wait their turn |
| One person's documents waiting or in progress | 200 | Settings: Each person's queue | Further uploads are refused until some finish |
| "Large" document | 200 pages | Settings: Large documents | Large ones run one at a time, one after every 5 smaller ones |
| Documents at the engine at once | Workers × `WORKER_CONCURRENCY` (at least 4) | Deploy; `PORTAL_ENGINE_MAX_IN_FLIGHT` | The rest wait in the portal's queue |
| A service's documents at once | None | Administration › Services: Most documents at once | The rest wait |
| Processes for one long document's page work | 3, from 50 pages | Engine: `PAGE_WORKERS`, `PAGE_WORKERS_MIN_PAGES` | Layout analysis, and the checks' reading of each page, are shared out over that many processes, each opening the PDF; the result is the same as one process's. If one fails, the work is done in one process |
| Engine time for one document | 1 hour | Engine: `TASK_TIME_LIMIT_S` | The document fails: "took too long to process" |
| Starts of one document whose worker was lost | 2 | Built in: `MAX_STARTS`, `app/worker/tasks.py` | The document fails ("stopped unexpectedly … too large for the engine's memory"), and its pages are given back. The queue hands a lost worker's document out again after `TASK_TIME_LIMIT_S` plus 15 minutes. Meanwhile a running document writes a heartbeat every 30 s; after 2 minutes without one, the engine reports it as lost (`worker_lost`), and the portal shows when it will be tried again |
| Portal's wait for one document | 12 hours | Built in: `JOB_TIMEOUT`, `portal/worker.py` | The document fails |
| veraPDF's answer to one check | 600 s | Engine: `VERAPDF_TIMEOUT_S` | The check counts as unavailable; the report says so |

## What the AI is asked

Past each of these the document is still processed; the part beyond the limit keeps what the layout rules
produced, and the report says how much was left.

| Limit | Default | Change | Past it |
|---|---|---|---|
| Figures described | 1,000 per document | Engine: `AI_IMAGES_MAX` | Later figures have no alt text, and fail the check for it |
| Form fields named by the AI | 200 per document | Engine: `AI_FIELD_LABELS_MAX` | Fields the page doesn't label past it keep their internal name; the review lists them |
| Tables analyzed | 100 per document | Engine: `AI_TABLES_MAX` | Later tables keep the rules' headers |
| Pages given an AI reading order | 200 per document | Engine: `AI_ORDER_PAGES_MAX` | Later multi-column pages keep the rules' order |
| Regions on one page for AI reading order | 60 | Built in: `MAX_ORDER_REGIONS`, `app/pipeline/enrichment.py` | That page keeps the rules' order |
| Formulas and other notation read | 100 per document | Engine: `AI_NOTATION_MAX` | The rest keep the rules' reading |
| Length of a notation block sent | 400 characters | Built in: `MAX_CHARS`, `app/pipeline/notation.py` | Longer blocks are treated as prose |
| OCR lines corrected | 300 per document | Engine: `OCR_AI_MAX_LINES` | The rest keep Tesseract's text |
| Figure tags a document arrived with, without a description, that are described | 1,000 per document | Engine: `AI_EXISTING_FIGURES_MAX` | The rest have no alt text, and fail the check for it; the report says how many and from which page |
| AI calls at once, per document | 4 | Engine: `AI_CONCURRENCY` | Calls queue |
| One AI call | 180 s, 3 tries | Engine: `AI_TIMEOUT_S`, `AI_MAX_RETRIES` | The call fails; that item keeps the rules' result |
| Length of an AI answer | 800 tokens (review: 1,500) | Engine: `AI_MAX_OUTPUT_TOKENS`, `VERIFY_MAX_OUTPUT_TOKENS` | A longer answer is cut off |
| Pages read to choose the kind of document | The first 3, plus pages spread through a long document | Settings: Let AI decide | Only those pages are looked at |

## Music

| Limit | Default | Change | Past it |
|---|---|---|---|
| Lines or examples read, document with music in it | 80 | Deploy: `MUSIC_MAX_EXAMPLES` | Later music is left as it was: not made figures, not read |
| Lines read, sheet music | 600 | Deploy: `MUSIC_MAX_SCORE_LINES` | The same |
| Wait for the reader | 240 s | Deploy: `MUSIC_WAIT_SECONDS` | Examples are described by the AI as pictures |
| Whole piece: extra wait per page | 90 s | Deploy: `MUSIC_PAGE_SECONDS` | No whole-piece file; lines are still read |
| Whole piece: size of the music pages | 40 MB | Deploy: `MUSIC_WHOLE_SCORE_MAX_MB` | No whole-piece file |
| A reading in one alt text | 8 measures or 1,200 characters | Built in: `app/pipeline/music.py` | Whole measures from the start, then the file's name |
| Shortest staff counted as music | 60 points | Built in, same file | Shorter ones are left alone |

## Formula reader

With `FORMULA_READER=on` on a cluster (#403):

| Limit | Default | Change | Past it |
|---|---|---|---|
| Formulas read from their pictures | 200 per document | Engine: `FORMULA_OCR_MAX` | The rest keep the AI's reading alone, unchecked |
| Wait for the reader | 180 s | Engine: `FORMULA_OCR_TIMEOUT_S` | The formulas are checked as without the reader |

## Checking the result

| Limit | Default | Change | Past it |
|---|---|---|---|
| Pages given the content checks | 400 | Engine: `VERIFY_MAX_PAGES` | Later pages get the structure checks only; the report says so |
| Items the AI reviewer looks at | 150 per file | Engine: `VERIFY_AI_MAX_ITEMS` | The rest aren't reviewed |
| Share of unflagged figures reviewed | 10% | Engine: `VERIFY_SAMPLE_RATE` | The others aren't reviewed unless "review every figure" is on |
| Findings of one kind listed | 25 | Built in: `_PER_ISSUE_CAP`, `app/verification/semantic.py` | Further ones are counted, not listed |
| Review items kept with a document | 300 | Portal: `PORTAL_REVIEW_FINDINGS_MAX` | The rest are in the JSON report only |
| Review items in the report PDF | 150 | Built in: `REVIEW_LIMIT`, `app/reports/pdf.py` | "And N more, listed in the portal" |
| Failed checks listed per veraPDF rule | 200 | `deploy/verapdf/validator.xml` | Counted, not listed |

## Adobe Autotag

| Limit | Default | Change | Past it |
|---|---|---|---|
| Pages and size sent in one request | 100 pages, 100 MB | Engine: `ADOBE_MAX_PAGES_PER_CALL`, `ADOBE_MAX_MB_PER_CALL` | The document is split, tagged in parts and joined |
| Requests at Adobe at once | 2 | Engine: `ADOBE_MAX_CONCURRENT` | The rest wait |
| One request | 20 minutes, 4 retries | Engine: `ADOBE_TIMEOUT_MINUTES`, `ADOBE_MAX_RETRIES` | The document fails; its pages are given back |

## Tag manager and editing

| Limit | Default | Change | Past it |
|---|---|---|---|
| Pages before the viewer opens one page at a time | 50 | Built in: `LONG_DOCUMENT_PAGES`, `web/src/viewer/draft.ts` | All pages asks first |
| Edits in one save or draft | 5,000 | Built in: `MAX_EDITS`, `portal/routers/versions.py` | The save is refused |
| Saves of one document being checked | 3 | Built in: `MAX_PENDING_PER_JOB`, same file | Wait for one to finish |
| Undo of a save into a reviewed version | 24 hours | Built in: `UNDO_HOURS`, same file and `portal/worker.py` | The file before is deleted |
| Text in one alt text or read-aloud text | 4,000 characters | Built in: `MAX_TEXT`, `app/tagging/editor.py` | The edit is refused |

## Help requests

| Limit | Default | Change | Past it |
|---|---|---|---|
| New requests by one person | 5 a day | Built in: `NEW_PER_DAY`, `portal/routers/help.py` | Refused until the next day |
| Files on one message | 5 | Built in: `MAX_FILES_PER_MESSAGE`, `portal/helpdesk.py` | Refused |
| Size of one file, and of all files on a request | 10 MB, 50 MB | Settings: Help requests | Refused |
| Length of a message | 5,000 characters | Built in: `MAX_BODY`, `portal/helpdesk.py` | Refused |
| Reopening a finished request | 14 days | Settings: Help requests | Ask again with a new request |

## Reports, files and sign-in

| Limit | Default | Change | Past it |
|---|---|---|---|
| How long files are kept | 30 days | Settings: How long things are kept | Files are deleted; the record stays |
| How long records are kept | 2,555 days | Settings: How long things are kept | Records are deleted |
| Hold before files marked done are removed | 24 hours | Settings: How long things are kept | Removed |
| Rows in an export | 100,000 | Built in: `MAX_EXPORT`, `portal/live_reports.py` | Narrow the dates |
| Report attached to an email | 5 MB | Built in: `MAX_ATTACHMENT`, `portal/report_views.py` | Left out, with a note to download it from the app |
| Logo | 512 KB | Built in: `LOGO_MAX_BYTES`, `portal/routers/branding.py` | Refused |
| Signed in without activity, and at most | 8 hours, 7 days | Settings: Sign-in | Sign in again |
| Sign-in codes asked for | 5 per address, 30 per IP address, every 15 minutes | Portal: `PORTAL_RATE_OTP_PER_EMAIL`, `PORTAL_RATE_OTP_PER_IP`, `PORTAL_RATE_WINDOW_SECONDS` | Refused until the window has passed |
| Sign-in codes checked | 50 per IP address, every 15 minutes | Portal: `PORTAL_RATE_VERIFY_PER_IP` | The same |
| Files downloaded, and exports, by one person | 600 and 120 an hour | Portal: `PORTAL_RATE_DOWNLOADS_PER_HOUR`, `PORTAL_RATE_EXPORTS_PER_HOUR` (0: off) | A plain 429; it clears within the hour |
