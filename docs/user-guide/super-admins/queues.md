# Queues

[Guide for super admins](README.md) › Queues

**Administration → Queues** shows everything that waits its turn: how much is waiting, in progress and failed in the
last 7 days, and how long the oldest item has waited.

## The queues

- **One per processing service and AI level** (documents).
- **Saved edits** (from the tag editor) and **AI reviews of saved versions**.
- **Email** (notifications: results, help requests, approvals).

**Sign-in codes are never queued and can't be paused**, so nobody can be locked out. Test emails also go at once.

## Pausing

**Pause** (with an optional reason) lets work already started finish and starts nothing new until **Resume**.

- Documents that use a paused service wait in its queue and go ahead on resume. They're never sent another way.
- People see "support has paused this processing option for now" on the document.
- AI reviews wait while the document's AI level is paused.

Use it when a provider is having problems, or before maintenance.

## Single items

Open a queue to see its items: **Waiting**, **In progress** and **Failed** (last 7 days).

- **Cancel** a waiting item. A canceled document gives the person's pages back, and they're emailed.
- **Try again** on a failed one. A document's pages are held again on the person's own limits, as when they sent it.
- A waiting **large document** has **Process now**: it goes next, ahead of the large-document rules, as soon as a
  slot is free (it still waits for a paused service or a daily limit).

Emails that fail for a temporary reason are retried automatically; ones that still fail wait in **Failed**.

Paused queues and failed emails show on **Attention**. A group's admins see when their documents wait for a paused
service. Pausing, resuming, retrying, canceling and **Process now** are recorded in the audit log.
