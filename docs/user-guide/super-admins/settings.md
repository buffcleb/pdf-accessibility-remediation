# Settings

[Guide for super admins](README.md) › Settings

**Administration → Settings**, one tab per topic, each with its own **Save**. Every change is recorded in the audit
log.

## Branding

- **Service name** and **organization name:** shown in the header, on the sign-in page, in the footer, in page
  titles, in emails and on the PDF report.
- **Email sender name.**
- **Accent color:** used for links and buttons with white text, so it must have at least 4.5:1 contrast against
  white (WCAG AA). The tab checks it and shows a preview; darker colors pass.
- **Logo:** PNG, JPEG or WebP, up to 512 KB, with its alt text. (SVG isn't accepted, because an SVG served from the
  app could carry script.)

![Administration, Settings, Branding tab, beside the tabs Help and messages, Email, Uploads and limits, and Sign-in and data: service name, organization name and email sender name; the accent color with its default; the logo, with Upload a logo and its alt text; and Save branding](../../images/admin-settings.png)

## Help and messages

- The **help email address** and the **link for requesting pages**.
- The messages people see when their email isn't set up for the service, when their account is disabled, and when
  they're out of pages.

## Email

- Which **provider** sends email (set on the server; shown here) and **Send test email**, which shows whether the
  message was accepted. Every test is in the audit log.
- **Notification emails:** the default for people who haven't chosen (as they happen, daily or off), per kind.
- **Scheduled email reports:** on or off for everyone, the **time zone** that decides when they go out and what
  "last month" means, and the **hour** they (and daily summaries) are sent.

## Uploads and limits

> **Changed 22 October 2026:** **Alerts** now lists the malware scanner's two alerts and the SIEM export's alert,
> for services that use them.

One sub-tab per topic, each with its own **Save**; moving to another with unsaved changes asks first:

| Sub-tab | Settings |
|---|---|
| **Uploads and queues** | Each upload, each person's queue, large documents |
| **Downloads and scripts** | Downloading as a zip, API tokens for scripts |
| **AI** | AI in the tag manager, Let AI decide the kind of document |
| **Checks** | PDF/UA validation (veraPDF) |
| **Help** | Help requests |
| **Alerts** | Alerts, security alerts |

- **Each upload:** the largest file, the most pages per document, the most files per upload.
- **Downloading as a zip:** the most documents in one zip download from a list of documents (25 by default).
- **API tokens for scripts:** whether groups may use tokens at all (off by default; each group's admins then switch
  them on for their group), how long a new token lasts (90 days) and the longest it may (365), how many one person
  may have (5), and after how long unused a token stops (90 days; 0 never). Switching tokens off stops every one at
  once.
- **Each person's queue:** how many of one person's documents are processed at once, and how many can be waiting
  (uploads beyond that are refused).
- **Large documents:** documents over a page count (200 by default; 0 turns it off) are mixed in, one after every
  few smaller ones (5 by default), with only so many running at once (1 by default). With nothing smaller waiting
  they go one after another. People see "Large document: it's processed in turn with smaller ones".
- **Help requests:** the largest attached file, all files on one request, and how long someone can reopen a
  finished request.
  **Open a help request when a document fails** (on by default) files every failure in the help queue under
  "The document couldn't be processed", with what the person was told, the error's name, and the engine's
  recorded detail as an internal note. Close the ones that are known limitations with a note. Documents
  someone stopped don't get one. With it off, people can still ask for help with a failed document.
- **AI in the tag manager: Describe with AI** (off by default) is the service default for the tag manager's AI alt
  text for a part of a figure: one AI call at the document's AI level each time, recorded on the document's cost
  (`describe_usd`, `describe_calls`). Each group, and each person in a group, can be set otherwise by their group
  admins or a super admin (Groups → the group's settings, and its People).
- **Kind of document: Let AI decide** offers *Let AI decide* among an AI level's kinds:
  - **Use Jev through OpenRouter:** an outside service, about $0.0001 per document, never used for local-only
    groups. It needs the engine's OpenRouter key.
  - **Local model:** a gateway model on your own servers, for local-only groups (and for everyone when Jev is off).
  - **Use the kind when at least … % sure** (60% by default); below that, General is used.
  - **Pages read** (3 by default): the first ones, plus more spread through long documents.
- **Alerts**: problems the service notices about itself, emailed to every super admin and shown on **Attention**:
  one email when one starts, a reminder while it lasts, and one when it clears.
  - **Queue not moving after** (60 minutes by default): documents are waiting and none has started or finished for
    this long. A busy queue that's moving never counts, and nor do documents waiting on purpose (a paused service,
    tomorrow's daily limit, a person's confirmation).
  - **Background worker stopped after** (15 minutes): the portal's worker hasn't completed a pass (documents, saves,
    emails) for this long.
  - **Engine without workers after** (10 minutes): the engine reports no workers, or can't be reached.
  - **Backup not finished after** (26 hours): only where the cluster runs the nightly backup; never where the hosts'
    own backups are used.
  - **A group's pool is running low below** (10% of its size, or 20 pages, whichever is larger): the group's admins
    (or the super admins, if it has none) get one email when a pool drops below; topping it up re-arms it. Attention
    uses the same threshold.
  - **Remind every** (4 hours): while a problem lasts.
  - Where uploads are checked for malware (`MALWARE_SCAN=clamav`, set on the server): **the malware scanner isn't
    answering** (every upload is refused while it's down; told after the same wait as the engine's), and **its
    signatures are out of date** (more than 3 days old, told after an hour, so a scanner that has just started
    doesn't alert). See [Operations › Malware scanning](../../operations/malware-scanning.md).
  - Where the audit log goes to a SIEM: **the audit log isn't reaching the SIEM**, when events have waited more than
    an hour to be sent ([System › Audit export](system.md#audit-export) shows how far it has got).
  - When the portal itself is down it can't email anyone: point an outside uptime check at `/api/v1/status`, which
    answers with an error (503) while there's a problem.
- **Security alerts**: patterns in the audit log someone should look at, emailed to every super admin, shown on
  **Attention** for a day and recorded in the audit log. **Security alerts on** turns them all off or on. The
  numbers: wrong codes for one address (5), addresses tried from one IP (10) and refused requests from one IP (10),
  each within **Sign-in patterns counted over** (15 minutes); codes asked for unknown addresses (20 an hour); other
  people's files downloaded by one person (25 an hour). A new super admin, a group allowed to send documents outside,
  shortened retention and a reused session cookie are told every time. The list, with what to do about each, is in
  [monitoring](../../operations/monitoring.md#security-alerts).
- **PDF/UA validation (veraPDF)**: how the checks run. The results are the same either way.
  - **As a service kept running** (the default): faster. If the service doesn't answer, the engine runs that check
    from the command line instead.
  - **From the command line in the engine:** Java starts for each check, about a second more each time.
  - **Also check PDF/UA-2** (on by default): shown in reports for information; the verdict is PDF/UA-1. Turning it
    off halves the number of checks. A PDF 2.0 document (what LaTeX's tagging and other recent tools write) can
    only be PDF/UA-2: it's labelled and judged as PDF/UA-2 whatever this says, and its results say "PDF/UA-2".

## Sign-in and data

- **Sign out after no activity for** (8 hours by default) and **Ask to sign in again after** (7 days), even for
  someone who stays active.
- **New groups → New members need approval:** whether groups created from now on start with it on (each group can
  change it).
- **Retention:** how long **files** are kept (30 days by default) and how long **records** (usage, ledger, sign-ins,
  audit) are kept before they're purged. **Files marked done** is the hold before the files of a document its owner
  marked done are removed (24 hours by default; 0 removes them at the next cleanup, within the hour).
