# Services, AI levels and pages

[Architecture](README.md) › Services, AI levels and pages

A **service** is either a way of processing documents ("Standard", "Adobe Autotag") or an **AI level** (a gateway
model such as "Local AI" or "Commercial AI"). Every document uses one processing service and, optionally, one AI
level, and is charged pages on each. An AI level can offer **kinds of document** (General, *Math and science* and so
on), each with its own models; kinds don't change what's charged.

## Who can use a service, and where pages come from

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/service-access-dark.svg">
  <img src="../images/diagrams/service-access.svg" alt="A service is used only after a super admin switches it on, a group admin enables it for the group (never an external one for a local-only group), it is given to everyone or person by person, and the person picks it; then pages are reserved on every service the document uses, from the global pool, the group pool and the person's limit at once; if any is short the upload is refused, naming it, otherwise it is queued.">
</picture>

Each service also has optional **cost guards**: most pages per document, most pages sent per day (extra documents
wait in the queue rather than fail), and confirm-before-send. It can show a **notice** at upload, for example where
documents go. Unmetered services count use but never limit it. An AI level also decides what a **Check only** run
costs: free, counted against the level, or made without AI.

**No AI** (**New 29 October 2026**, #659) isn't a service: it's the AI level key `none` (`portal/no_ai.py`), taken at
upload (`ai_tier`, `per_file`), on API tokens and as a group's fixed level (`ai_mode` `fixed`, `ai_level` `none`). It's
offered top down: the `no_ai_enabled` setting (super admins), then `groups.no_ai` (`off`, `on`, or `default`:
preselected ahead of the group's default level; migration 0054, inherited with the other AI settings). A local-only
group can use it whatever its levels are. `_plan` in `portal/routers/jobs.py` gives such a document no tier and
`jobs.no_ai`; its pages are charged on the processing service only. A document with no tier for any reason (No AI, no
level, no default, Adobe without AI alt text, a check whose level is set to make checks without AI) goes to the engine
with `ai_provider` `none` and `verify_ai`, `ocr_ai` and `notation_ai` off (`portal/engine.py`): never to the
engine's fallback (`PORTAL_ENGINE_AI_PROVIDER`). The engine lists each figure left without alt text as a `high`
finding ("figure has no alt text", `app/verification/semantic.py`), every one with its own row rather than folded
into "and N more like this" past 25, and both reports say the document was made
without AI. Afterwards the tag manager's AI (Describe and Suggest tags with AI, a save's AI review, a later review)
is refused for it with `no_ai_document` (`portal/routers/versions.py`).

A group's admins (or the super admins, if it has none) are emailed once when its pool runs low, below a share of
its size or a number of pages (Settings); topping it up re-arms the alert (`portal/pool_alerts.py`, #358).

**Subgroups** (#431, added 26 October 2026) have no pool. A subgroup member's document reserves its pages on the
global pool, the **parent's** pool, the subgroup's own row (checked against its optional `cap_pages`, refusal scope
`subgroup`) and the person's limit, all at once. Rows are locked in one order: services, the parent's group rows,
the subgroup's, the person's. A ledger entry's `group_id` is the pool's group (the parent) and `subgroup_id` names
the subgroup; reports file an entry under `coalesce(subgroup_id, group_id)` (`LEDGER_GROUP`). Top-ups and pool
corrections on a subgroup are refused. `services.pool_left` gives what a group can still use (a subgroup's: the
parent's pages left, within its limit) for remaining pages, balances, Attention, summaries and low-pool emails. A
subgroup can use a service only when its parent has it on too. Its settings follow the parent's unless it sets its
own (`portal/inherit.py`, `groups.overrides`).

At upload, a signed or certified PDF is held for the person's confirmation like a service that asks for it, since
remediating breaks the signature (#339). A dynamic XFA form is refused: it has no standard form to tag (#360).

## One document's path

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/document-path-dark.svg">
  <img src="../images/diagrams/document-path.svg" alt="A person uploads with a processing service, AI level and kind; the portal checks access and counts pages in a limited child process, holds the document if the service asks for confirmation or the PDF is signed, then reserves pages; the worker orders the queue, optionally has the engine classify the document, and submits it; the engine's AI calls go through the gateway, which returns answers and costs; the worker polls, fetches the files, stores the versions, commits or releases the pages, and emails the person.">
</picture>

Every ledger entry records the service, the scope (global or group), the person, the job, any ticket, override or
reason, and the balances after it. Super admins can also **correct** a person's usage. Every change to pools,
limits, grants and services is in the audit log.

## Queues

The portal worker decides what goes to the engine next, and in what order:

- each person has a limit on documents processed at once, so one person's batch doesn't hold everyone up;
- **large documents** (over a page count) take turns with smaller ones, a few at a time;
- a service with a **daily page limit** holds documents for the next day once it's reached;
- a super admin can **pause** a service's queue; its documents wait and go ahead on resume;
- a service or AI level can have a limit on its documents at the engine at once;
- the engine has a limit on documents in flight at once.

Edits from the tag editor, AI re-reviews and email have their own queues. Sign-in codes are never queued.
