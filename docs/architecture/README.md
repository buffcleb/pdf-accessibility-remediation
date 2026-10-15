# Architecture

How the service is built: its parts, how a document moves through them, and why it's designed this way.

## Contents

1. [Services, AI levels and pages](services-and-quotas.md): what a service is, who can use it, how pages are
   counted, one document's path
2. [The engine pipeline](engine-pipeline.md): how a PDF is analyzed, tagged and checked, step by step
3. [Verification](verification.md): the three layers that check every output
4. [The Adobe route](adobe-route.md): sending documents to Adobe PDF Services, and what's done with the result
5. [The tag manager and editor](tag-editor.md): human review in the browser
6. [Security and privacy](security-and-privacy.md), and the [design decisions](design-decisions.md) behind it all
7. [Source map](source-map.md): where everything is in the code

## The parts

- **The web portal:** sign-in, groups, services and page quotas, uploads, results, reports, help requests and the
  tag manager and editor. It's the only part people use directly.
- **The remediation engine:** a headless service that tags a PDF for accessibility (PDF/UA-1), validates it,
  checks its own work and returns the files with reports. Only the portal can reach it.
- **The AI gateway (LiteLLM):** one endpoint in front of every AI model the engine may use, local or cloud, with
  spend tracking and a budget backstop.

A second processing service, **Adobe Autotag**, sends documents to Adobe PDF Services for tagging and returns
Adobe's file as it is, checked by the engine. It's off until a super admin switches it on.

## System context

```mermaid
flowchart LR
  B["Browser<br/>React app"] -- "HTTPS<br/>access token + refresh cookie" --> WEB["Web<br/>nginx: app + /api proxy"]
  WEB --> API["Portal API<br/>FastAPI"]
  subgraph Portal ["Portal"]
    API --> DB[("PostgreSQL<br/>users, groups, services,<br/>quotas, ledger, jobs,<br/>versions, audit")]
    API --> RP[("Redis<br/>rate limits")]
    API --> S3[("Object store<br/>S3 API<br/>encrypted at rest")]
    PW["Portal worker"] --> DB
    PW --> S3
    API -- "sign-in codes" --> MAIL["Mail<br/>SMTP / SES / API"]
    PW -- "notifications,<br/>reports" --> MAIL
  end
  PW -- "internal HTTP + API key:<br/>submit, poll, fetch files" --> EAPI
  subgraph Engine ["Engine"]
    EAPI["Engine API<br/>FastAPI"] --> ER[("Redis<br/>queue")]
    ER --> EW["Workers<br/>Celery"]
    ER -- "queue: adobe" --> AW["Adobe worker"]
    EW --> VP["veraPDF service<br/>(kept running; command line<br/>in the worker as a fallback)"]
    EW --> TS["Tesseract OCR"]
    EAPI --- JS[("Shared job<br/>folder")]
    EW --- JS
    AW --- JS
    EW -- "all AI calls,<br/>tagged job + level" --> GW["AI gateway<br/>LiteLLM"]
    GW --- GDB[("LiteLLM DB<br/>spend, keys")]
  end
  GW --> LOC["Local models<br/>LocalAI / vLLM / Ollama"]
  GW -.-> CLOUD["Cloud and commercial<br/>models (opt-in levels)"]
  AW -. "opt-in service,<br/>rate limited" .-> ADOBE["Adobe PDF Services<br/>Autotag"]
```

| Component | Technology | Role |
|---|---|---|
| Web app | React + Vite, served by nginx | All screens, including the tag manager and editor (pdf.js). nginx also proxies `/api` to the portal API |
| Portal API | FastAPI, SQLAlchemy 2, Alembic | Accounts, groups, services, quotas, uploads, versions, help requests, reports, audit |
| Portal worker | Python loop | Sends queued jobs, edits and reviews to the engine, polls them, copies results into storage, settles quotas, sends email, applies retention |
| Database | PostgreSQL 16 | Everything the portal knows. The quota ledger is append-only (a trigger refuses updates) |
| Object store | S3 API (SeaweedFS locally; AWS S3, Cloudflare R2 and so on elsewhere) | Input and output files under `groups/{group}/users/{user}/jobs/{job}/…`, encrypted at rest |
| Engine API and workers | FastAPI, Celery, Redis | The remediation pipeline, tag edits, re-reviews and the kind-of-document classifier. Never exposed to the internet |
| AI gateway | LiteLLM + its own PostgreSQL | Routes each call to the model behind an AI level; reports cost per call; budget backstop; admin UI |
| Validator | veraPDF 1.30, as a service kept running (its REST server) | PDF/UA-1 (and informational PDF/UA-2) before and after. A check that the service doesn't answer runs from the command line in the worker ([details](../operations/configuration.md#verapdf-service)) |
| Scaling (Kubernetes) | KEDA, the Horizontal Pod Autoscaler | Workers follow the portal's queue; veraPDF and the AI gateway follow their CPU use. Services and AI levels can limit their documents at once ([details](../../deploy/k8s/README.md#scaling-with-the-queue)) |
| OCR | Tesseract 5 | Text of scanned pages, with a confidence per word |
| Report PDF | WeasyPrint | The accessible PDF report (PDF/UA-1) |
| Adobe worker | Celery on the `adobe` queue | Adobe Autotag jobs: split, send, poll, merge, then the same checks as every output |

## Where to go next

- To run it: [Deployment](../deployment/README.md) and [Operations](../operations/README.md).
- To call the engine directly: [API reference](../api/README.md).
- How it was designed and built, phase by phase: [Design notes](../design/README.md).
