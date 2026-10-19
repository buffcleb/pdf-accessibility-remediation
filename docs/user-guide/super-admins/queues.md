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
- **Stop** an item that's in progress: a document that's stuck or was sent by mistake, a save being checked, a
  review being run. It ends at once and frees its place in the engine. A stopped document gives the person's pages
  back, and they're emailed; they can send it again.
- **Restart** an item that's in progress but not moving: its engine worker was lost, or a deploy cut it off. The
  engine is told to stop its copy, and the item goes back to the queue, keeping its place. A document's pages stay
  held as they were, so nothing is charged again, and nobody is emailed.
- **Remove** a failed item from the list once you've dealt with it. Nothing about the item changes (a document
  stays in the person's documents as failed, with its history); it just stops being listed and counted here. If
  it's tried again and fails again, it's listed again.
- **Try again** on a failed one. A document's pages are held again on the person's own limits, as when they sent it.
- A waiting **large document** has **Process now**: it goes next, ahead of the large-document rules, as soon as a
  slot is free (it still waits for a paused service, a daily limit or a service's "most documents at once").

Emails that fail for a temporary reason are retried automatically; ones that still fail wait in **Failed**.

Paused queues and failed emails show on **Attention**. A group's admins see when their documents wait for a paused
service. Pausing, resuming, retrying, canceling and **Process now** are recorded in the audit log.

## Live: everything at once

**Queues → Live** is one list across every queue, for following what is happening now or finding one person's
document without knowing which queue it is in.

- **In progress** items come first, each with the **stage** it has reached and how long it has been running;
  documents also show how far through they are. Then what is **waiting**, longest first, with the reason (paused,
  a daily limit, no free place, a large document held back). Then what **finished, failed or was canceled** in
  the last 10 minutes, so an item doesn't vanish the moment it's done.
- **Search** by a document's name, a person's email address or an email's subject. Narrow to a **group**, to one
  **queue**, or to one state with the buttons above the list; the numbers on them count what matches. Email
  belongs to no group, so it is left out when a group is chosen.
- The list **updates every 3 seconds**. **Pause updates** holds it still while you read a row; **Resume updates**
  catches up.
- **Stop**, **Restart**, **Cancel** and **Process now** work as in a queue's own list. To try a failed item again or
  take it off the failed list, open its queue under **By queue**.

Progress is by stage: a long stage (the AI writing, the AI review) stays at one figure until it ends. The view
takes up to 300 items of each kind; narrow it if it says there is more.

## Re-running a document

Any finished, failed or stopped document has **Re-run** on its page, for super admins. It makes a new document for
the same owner, with the original file and the same options, and puts it in the queue. The old document, its
versions and its edits stay as they are, and each links to the other. Use it when an engine fix should be tried on
a document that already went through.

Pages aren't charged again unless you tick **Charge the owner's pages again**: then they're held on the owner's
limits as for an upload, and refused if the owner doesn't have enough. The reason you give, if any, goes into the
audit log with the re-run (`job.rerun`). A document whose files have been deleted can't be re-run.

**Ignore the document's existing tags** re-runs it tagged afresh from the page, its own tags set aside: for a PDF
whose tags turned out poor. It starts as the original was run, ticked or not.

The document page also has a **Run details** tab for super admins: what the run cost and used, the limits it
reached, time by stage, and the engine's side (its job ID, route, error and warnings).
