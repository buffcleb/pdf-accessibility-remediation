# Guide for super admins

For the people who run the service: you set it up, decide which processing options and AI levels exist, offer
them to groups, add pages, handle help requests, watch the queues and read the service-wide reports. Super admins
can do everything a [group admin](../group-admins/README.md) can, for every group, except edit tags in the tag
manager and sign off review items: those stay with each document's owner and their group admins.

Installing and configuring the servers (environment variables, AI models, email providers, backups) is in
[Operations](../../operations/README.md); this guide covers what you do in the portal.

## Contents

1. [First-time setup](first-time-setup.md): a checklist from an empty service to the first group using it
2. [Groups and people](groups-and-people.md): groups, domains, group admins, pages and top-ups, corrections
3. [Services and AI levels](services-and-ai-levels.md): processing options, AI levels and kinds of document, cost
   guards, checks without AI
4. [Settings](settings.md): branding, messages, email, upload limits, "Let AI decide", sign-in and retention
5. [Help requests](help-requests.md): taking, answering and finishing requests, corrected files, pages back
6. [Queues](queues.md): what's waiting, pausing and resuming, retrying and canceling
7. [Reports and audit](reports-and-audit.md): service-wide reports, cost, the audit log, the AI gateway's admin UI
8. [System](system.md): the service's health from its monitoring, its pods, its alerts, and Grafana

## Becoming a super admin

The first super admins come from the `PORTAL_BOOTSTRAP_SUPER_ADMINS` setting (a list of email addresses): each
becomes a super admin the first time it signs in. After that, super admins make others super admins in
**People (all groups) → Manage**. The last super admin can't be removed.

## The Administration menu

As a super admin, the menu on the left has: **Attention**, **Groups**, **Help requests**, **Services and AI
levels**, **People (all groups)**, **Queues**, **System**, **Reports**, **Settings** and **+ Offer a service to a
group**. When the AI gateway's admin UI is configured, a link to it appears in the menu and on Services and AI levels.

![Administration, Attention for a super admin: Across every group and service with 1 document failed in the last 7 days (reason: the engine refused it), Open its help request and Acknowledge; 2 help requests waiting for support, with Open Help requests; an Acknowledged section; and the access check, Why can't this person use a service?](../../images/admin-attention.png)

**Attention** lists what a group's admins see for every group, and the service's own: its alerts, security alerts,
and help requests waiting for support (new, or the person's last message unanswered). Each item links to what it's
about: the document or the help request when there's one, otherwise the list of them (Queues, the Documents
report, the audit log) or the page to fix it on (System, a service, a group's pages).

Summaries and notices (failed documents, emails that couldn't be sent, security alerts and the like) can be
**acknowledged**, for every super admin, until something new happens; the group's admins have their own list to
acknowledge. The service's alerts and help requests waiting can't be: they go when they're fixed or answered, and an
alert's reminder emails go on until then.
