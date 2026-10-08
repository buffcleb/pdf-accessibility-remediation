# Services, AI levels and pages

[Architecture](README.md) › Services, AI levels and pages

A **service** is either a way of processing documents ("Standard", "Adobe Autotag") or an **AI level** (a gateway
model such as "Local AI" or "Commercial AI"). Every document uses one processing service and, optionally, one AI
level, and is charged pages on each. An AI level can offer **kinds of document** (General, *Math and science* and so
on), each with its own models; kinds don't change what's charged.

## Who can use a service, and where pages come from

```mermaid
flowchart TD
  subgraph Optin ["Who can use a service"]
    G1["Super admin:<br/>switched on globally"] --> G2["Group admin:<br/>enabled for the group<br/>(blocked if the group is local-only<br/>and the service is external)"]
    G2 --> G3["Given to everyone in the group,<br/>or person by person"]
    G3 --> G4["Person picks it at upload<br/>(group's preselected option by default,<br/>or the group's fixed AI level)"]
  end
  subgraph Pages ["Where the pages come from (per service)"]
    P1["Global pool<br/>(optional)"] --- P2["Group pool<br/>(ticketed top-ups)"] --- P3["Person's limit<br/>(own or group default)"]
  end
  G4 --> R{"Reserve pages on every<br/>service the document uses,<br/>at all three levels at once"}
  P1 & P2 & P3 -.-> R
  R -- "any level short" --> X["Refused, naming the service<br/>and the level that ran out"]
  R -- ok --> Q["Queued"]
```

Each service also has optional **cost guards**: most pages per document, most pages sent per day (extra documents
wait in the queue rather than fail), and confirm-before-send. It can show a **notice** at upload, for example where
documents go. Unmetered services count use but never limit it. An AI level also decides what a **Check only** run
costs: free, counted against the level, or made without AI.

## One document's path

```mermaid
sequenceDiagram
  actor U as Person
  participant P as Portal API
  participant W as Portal worker
  participant E as Engine
  participant G as AI gateway
  U->>P: upload (processing service, AI level, kind)
  P->>P: access checks, page counts, per-document limit
  alt service asks for confirmation
    P-->>U: held, nothing reserved
    U->>P: confirm
  end
  P->>P: reserve pages (ledger: reserve)
  W->>W: queue order: per-person concurrency,<br/>large documents, daily caps, paused queues
  opt "Let AI decide"
    W->>E: classify (first pages' text)
  end
  W->>E: submit (the level's or kind's gateway models)
  E->>G: AI calls tagged job + level
  G-->>E: answers + cost per call
  W->>E: poll, then fetch files
  W->>P: store versions and AI cost, ledger: commit (or release on failure)
  W-->>U: email (as it happens, or in a daily summary)
```

Every ledger entry records the service, the scope (global or group), the person, the job, any ticket, override or
reason, and the balances after it. Super admins can also **correct** a person's usage. Every change to pools,
limits, grants and services is in the audit log.

## Queues

The portal worker decides what goes to the engine next, and in what order:

- each person has a limit on documents processed at once, so one person's batch doesn't hold everyone up;
- **large documents** (over a page count) take turns with smaller ones, a few at a time;
- a service with a **daily page limit** holds documents for the next day once it's reached;
- a super admin can **pause** a service's queue; its documents wait and go ahead on resume;
- the engine has a limit on documents in flight at once.

Edits from the tag editor, AI re-reviews and email have their own queues. Sign-in codes are never queued.
