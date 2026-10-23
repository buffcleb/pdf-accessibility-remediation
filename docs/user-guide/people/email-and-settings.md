# Email and settings

[Guide for people submitting documents](README.md) › Email and settings

**Settings** (top right) is where you choose what the service emails you. Choices save as you make them.

## Email notifications

For each kind of email: **As they happen** (the default), **Once a day** (one email with all of them, at the
service's send time) or **Off**.

![The Settings page: tabs for Email notifications, Email reports and Signed-in devices; Your documents and Help requests each As they happen, Once a day or Off; Email reports for Your documents, Monthly, Weekly, Daily or Off, with Email me a sample; and Signed-in devices with Sign out of all devices](../../images/my-settings.png)

| Kind | What it's about |
|---|---|
| **Your documents** | A document is done, couldn't be processed, or was canceled by support |
| **Help requests** | Replies and changes on your help requests, and reminders when one is waiting for your reply. For support staff, new requests and replies |
| **Admin notices** (admins) | Someone in your group is waiting for approval |

- Sign-in codes and news about your own account (approved or declined) always come at once.
- Everything is still in the portal, whatever you choose.
- With help-request emails off, a request waiting for your reply still closes after 10 days.

**Skip emails about things you have open:** under **Your documents** and **Help requests**, a checkbox skips the
email when you already had that document (or My documents) or that help request open in the portal when the news
came. It's on for help requests, so chatting back and forth doesn't fill your inbox, and off for documents,
because many people keep those emails as a record. Only a page that's actually in view counts, not a background
tab.

## Email reports

A summary in your inbox, so you don't need to sign in to keep track. Each is **Monthly** (the default),
**Weekly**, **Daily** or **Off**:

- **Your documents** (everyone): documents processed and how many pass, pages used and left per option, and
  anything waiting for you (documents to confirm, help requests waiting for your reply). Nothing happened and
  nothing waiting means no email.
- **Group summary** (group admins): pages used and left against the pool, the most active people, how many
  documents pass, open help requests and people waiting for approval.
- **Service summary** (super admins): pages by group and option, AI cost and Adobe pages, days a daily limit was
  reached, quality and the most common remaining failures, and the help queue.

Monthly reports cover the month before and arrive on its first working day; weekly ones cover Monday to Sunday;
daily ones cover the day before. Reports with the same frequency come together in one email. **Email me a sample**
sends last month's now, so you can see what it looks like. Every email has a link to stop that report, which works
without signing in.

## Scheduled report views (admins)

If you set up scheduled views of the live reports (see the [group admin guide](../group-admins/reports.md)), they're
listed here: change how often, who gets it and the CSV, email yourself a sample, or delete. Views others send you
are listed too, with **Stop getting it**.

## API tokens

> **Changed 22 October 2026:** this section now covers **Confirm sending automatically**, and that a group admin can
> revoke a token.

When your group allows it, **API tokens** lets you make tokens for scripts that send documents, for example a
nightly job that sends a folder of PDFs.

- **What it sends to:** each token sends to one processing option and AI level (and kind of document), chosen when
  you make it. A script can't change that.
- **What it may do:** every token can submit. Tick **Check their status** for a script that needs to know when
  documents are done.
  - Downloads are optional: **Everything** (each document's zip) or **Chosen files** (for example just the
    remediated PDF and the report).
  - A script can ask only for the finished documents it hasn't downloaded yet, so a sending job and a separate
    fetching job can each run on their own schedule.
- **Confirm sending automatically:** offered when the token's option asks before documents go to an outside service.
  Ticked, the script's documents go without asking; otherwise they wait in **My documents** for you to send them.
- **Copy it at once:** the token is shown once, when you make it. If you lose it, revoke it and make another.
- **Your documents:** everything a token sends appears in **My documents**, as if you'd uploaded it, with the same
  pages, emails and review.
- **When tokens stop:** a token stops working when you (or one of your group's admins) revoke it, when it expires (90 days unless you choose
  otherwise), after a long time unused, or if your group or the service switches tokens off. You get an email a
  week before one expires, and when one stops unused.

[Scripting](scripting.md) has examples with `curl`.

## Signed-in devices

**Sign out of all devices** signs you out of every browser and device at once, this one too: for a laptop you no
longer have, or a shared computer you left signed in. You'll need a new code to sign in again.
