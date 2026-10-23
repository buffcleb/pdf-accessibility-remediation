# The admin area

[Guide for group admins](README.md) › The admin area

**Administration** (in the header) is shared by group admins and super admins; each sees what their role allows.
As a group admin, the menu on the left has:

- **Attention:** what needs action now (below).
- **My group** (or **My groups** if you look after more than one): your group's own pages.
- **+ Offer a service to a group:** a guided set-up for giving a service to your group (see
  [Services and pages](services-and-pages.md)).

**Hide menu** folds the menu away for more room; the ☰ button brings it back, with a count when something's
waiting. The choice is remembered.

A badge on **Administration** counts what's waiting for you, such as people waiting for approval.

![A group admin's view of Northfield University: tabs for Overview, Services and pages, People, Help requests (2), Documents, Reports and Group settings; Needs attention with 1 document failed in the last 7 days (reason: the engine refused it), a link to its help request and Acknowledge, and an Acknowledged section; Standard, Adobe Autotag and Commercial AI pages left; the access check; and the start of the page ledger](../../images/group-admin.png)

## Attention

> **Changed 22 October 2026:** every item links to what it's about, failures give their reasons in words, and
> summaries and notices can be acknowledged.

The first page: what needs action, most serious first, each with what to do about it and a link to what it's
about. One document opens it (or its help request, when one is open); several open the group's documents, filtered
to them.

**Acknowledge** a summary or notice you've dealt with (documents that failed, held or lost their engine worker, the
AI not being available). It's hidden for all the group's admins, and the audit log records who and when. It comes
back when something new happens, counting only what's new ("not counting 2 acknowledged earlier"). **Acknowledged**,
below the list, shows what was acknowledged, by whom and when, with **Show again**. Problems that need fixing (an
empty pool, people waiting for approval) can't be acknowledged: they go when they're fixed.

- **People waiting for approval.**
- **Pools that are empty or running low.** You're also emailed once when one drops below the service's threshold
  (10% of the pool or 20 pages, whichever is larger, unless your administrators set otherwise), so you can request
  more in time. With an empty pool, everyone sees 0 pages left, whatever their own limit.
- **Personal limits larger than what's left in the pool**, which can't actually be used.
- **Documents** held for confirmation for over a day, waiting for a daily limit, large ones waiting over 2 hours, or
  processing for over 2 hours.
- **Documents that failed** in the last 7 days, with the most common reasons in words.
- **Documents whose engine worker was lost** partway through (it ran out of memory or its pod restarted). The
  document's page says when the engine will try it again, or that it won't.
- **Documents where the AI wasn't available.**
- **Waiting for a paused service:** support has paused an option your group's documents use.

## Why can't this person use a service?

Below the list, this check takes an **email address** and a **service** and runs every check in order:

1. Does the account exist, and is it active?
2. Is it in a group, and is the group active?
3. Is the service switched on?
4. Do the group's data rules allow it (local-only)?
5. Is the service enabled for the group?
6. Has the person been given it?
7. Are there pages: in the service's global pool, the group's pool, and their own limit?

The first failing check is shown with its fix. It's the quickest way to answer "it says I can't upload".

## Your group's pages

**My group** opens on its own tabs:

| Tab | What's there |
|---|---|
| **Overview** | The group's attention list, pages left for each service, the access check and the page ledger (every change to the group's pages) |
| **Services and pages** | Each service: on or off, who has it, the pool, the default limit per person ([more](services-and-pages.md)) |
| **People** | Waiting for approval, everyone's access and limits per service, account settings ([more](people-and-approval.md)) |
| **Documents** | The group's documents ([more](documents-and-help.md)) |
| **Help requests** | The group's help requests ([more](documents-and-help.md#help-requests)) |
| **Reports** | Live reports for the group ([more](reports.md)) |
| **Group settings** | Approval, AI for uploads, local-only, marking external options ([more](group-settings.md)) |

## Unsaved changes

> **Changed 22 October 2026:** forms in a dialog ask before closing with changes not saved.

If you've changed something and not saved it, the portal asks before you leave the page: **Stay on this page** or
**Leave without saving**. Forms show "Unsaved changes" next to their Save button.

Most edit forms (a service's settings, top-ups, adding things) open in a dialog over the page. Escape, **×** or a
click outside it closes it at once when nothing has changed; with changes, it asks first: **Save**, **Keep editing**
or **Discard changes**.
