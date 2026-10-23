# Adobe Autotag

[Operations](README.md) › Adobe Autotag

The "Adobe Autotag" processing service (seeded switched off) sends documents to Adobe PDF Services for tagging. How it
works: [The Adobe route](../architecture/adobe-route.md). Two switches must both be on: the engine's
`ADOBE_ENABLED=true`, and the service in **Administration → Services and AI levels**.

## Engine settings

Adobe doesn't publish its limits, so take them from your contract.

| Setting | Default | |
|---|---|---|
| `ADOBE_CLIENT_ID`, `ADOBE_CLIENT_SECRET` | | One credential from the Adobe Developer Console (PDF Services API) |
| `ADOBE_BASE_URL` | `https://pdf-services-ue1.adobe.io` | `https://pdf-services-ew1.adobe.io` keeps documents in Adobe's EU region |
| `ADOBE_RPM` | 25 | API requests per minute, shared by all Adobe workers (token, upload, start, status polls, deletes) |
| `ADOBE_MAX_CONCURRENT` | 2 | Documents or parts at Adobe at once, shared by all Adobe workers |
| `ADOBE_MAX_PAGES_PER_CALL`, `ADOBE_MAX_MB_PER_CALL` | 100, 100 | Larger documents are split, and the tagged parts merged back |
| `ADOBE_MAX_RETRIES` | 4 | For "too many requests" and server errors. Running out of transactions (Adobe codes 429001/429002) is never retried |
| `ADOBE_POLL_SECONDS`, `ADOBE_TIMEOUT_MINUTES` | 3, 20 | Status polling, and the limit per part |
| `ADOBE_GENERATE_REPORT` | true | Keep Adobe's own accessibility report (an Excel file) with the job |
| `ADOBE_SHIFT_HEADINGS` | false | Passed to Adobe as `shiftHeadings` (Adobe's option to renumber heading levels) |

## Before the first real document

1. Start with the **fake Adobe** (below) to check the stack end to end.
2. Put the real credential in the engine's settings, set `ADOBE_ENABLED=true`, and restart the engine API and the Adobe
   worker. `GET /api/v1/health` shows `adobe.enabled` and `adobe.configured`.
3. In the portal, set the service's cost guards first: a small **most pages per document** and **most pages per day**,
   and keep **confirm before sending** on. Give the service to one test group with a small page pool, then switch it
   on. The group needs pages in its **Adobe pool**: per-person limits alone don't give anyone pages.
4. Submit a short document and check its "Adobe tagged" version, Adobe's report and the review.

**AI alt text.** Adobe writes no alt text, so uploads to Adobe Autotag have "Write alt text for images with AI" ticked
by default. That needs an AI level the person can use, and it charges that level's pages, given back if the AI wasn't
needed. The same AI reads specialist notation (math, chemistry) onto Adobe's tags in the improved copy.

**The improved copy is the document people get** (#499): the main download, the verdict and tiles, Needs review and
the tag manager are about it, and Adobe's file as it came back is a choice in the downloads and the tag manager.
Documents finished before that change still show Adobe's file's verdict until they're brought up to date, once, after
the deploy (`--dry-run` first to see how many):

```bash
kubectl -n pdf-remediation exec deploy/portal-api -- python -m portal.cli resummarize-improved --dry-run
kubectl -n pdf-remediation exec deploy/portal-api -- python -m portal.cli resummarize-improved
```

It reads each document's JSON report from storage and changes only its verdict, failures, before-and-after counts
and review items. Documents whose files were removed keep what they had.

Each job records what it sent (`cost.adobe`: pages sent, parts, requests, retries, seconds at Adobe), shown to super
admins. If Adobe says the account is out of transactions, jobs fail with "Adobe's usage limit…" and the pages are given
back.

## The fake Adobe

`app/adobe/fake.py` speaks Adobe's REST protocol and tags with the engine's own tagger, so nothing leaves the machine:

```bash
docker compose --profile fake-adobe up -d --build fake-adobe
```

Then set `ADOBE_BASE_URL=http://fake-adobe:8090`, any `ADOBE_CLIENT_ID` and `ADOBE_CLIENT_SECRET`, and
`ADOBE_ENABLED=true`. To try the error handling, set on the fake-adobe service: `FAKE_ADOBE_THROTTLE_EVERY=3` (every
third request answers "too many requests"), `FAKE_ADOBE_QUOTA=true` (out of transactions),
`FAKE_ADOBE_FAIL=INTERNAL_SERVER_ERROR` or `FAKE_ADOBE_POLLS=5`. A batch run against it:
`python -m harness.run_harness samples --route adobe`.
