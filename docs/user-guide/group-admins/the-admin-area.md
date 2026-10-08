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

## Attention

The first page: what needs action, most serious first, each with what to do about it.

- **People waiting for approval.**
- **Pools that are empty or running low.** With an empty pool, everyone sees 0 pages left, whatever their own
  limit.
- **Personal limits larger than what's left in the pool**, which can't actually be used.
- **Documents** held for confirmation, waiting for a daily limit, stuck or failing.
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

If you've changed something and not saved it, the portal asks before you leave the page: **Stay on this page** or
**Leave without saving**. Forms show "Unsaved changes" next to their Save button.
