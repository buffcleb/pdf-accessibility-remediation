# Guide for service admins

[User guide](../README.md) › Guide for service admins

> **New 23 October 2026:** the service admin role (#564).

For the people who help run the service day to day, alongside its super admins. You look after every group, add
pages, answer help requests, watch the queues and read every report. You don't change the service itself: its
settings, its services and AI levels, and who is a super admin or service admin stay with the super admins.

You see the same screens as a super admin, with less on some of them. So this guide says what's yours and points
to the [super admins' guide](../super-admins/README.md) for how each screen works. You're also an ordinary user,
so the [guide for people submitting documents](../people/README.md) applies to you too.

## Becoming a service admin

A super admin makes you one, in **People (all groups) → Manage → Make service admin**, and can take it away the same
way. You don't have to belong to a group, as a super admin doesn't. If you do, you can submit your own documents with
your group's pages. Every super admin is told by a security alert when someone is made a service admin.

## The Administration menu

![Administration for a service admin: the menu has Attention, Groups, Help requests, People (all groups), Queues, System, Reports and + Offer a service to a group, without Services and AI levels or Settings; Attention shows Across every group and service, with 1 document failed in the last 7 days and 2 help requests waiting for support, then the access check, Why can't this person use a service?](../../images/service-admin.png)

The menu on the left has **Attention**, **Groups**, **Help requests**, **People (all groups)**, **Queues**,
**System**, **Reports** and **+ Offer a service to a group**. **Services and AI levels** and **Settings** aren't
there: those are the super admins'.

## What's yours, and what isn't

| Area | You can | Left to super admins |
|---|---|---|
| **Attention** | See and acknowledge what needs attention across every group and service, and check why someone can't use a service ([Attention](../super-admins/README.md#the-administration-menu)) | |
| **Groups** | Open any group, as its admins see it; create a group; rename it; change its email domains and the people allowed by address; make and remove its group admins ([Groups and people](../super-admins/groups-and-people.md)) | Disabling a group, or enabling it again |
| **Pages** | Add pages to a group's pool under a ticket, or as an override without one (flagged, with your comment); correct the pool; correct a person's usage ([Pages and top-ups](../super-admins/groups-and-people.md#pages-and-top-ups)) | A service's own pool, shared by every group |
| **Offering a service** | Offer any service or AI level that's switched on to a group, with pages, in **+ Offer a service to a group** | Switching a service on for everyone |
| **People (all groups)** | Find anyone; move them to another group; correct their usage; disable their account; sign them out everywhere ([People](../super-admins/groups-and-people.md#people-all-groups)) | Making super admins and service admins; a person's data (export, removal); super admins' and service admins' own accounts |
| **Documents** | Open any group's documents and files, including the engine's own reports and the **Run details** tab. Each file you open of someone else's is in the audit log, as for any admin. Rerun a document; remove files or delete a document at once | |
| **Help requests** | Everything support does: take a request, reply, write internal notes, change its status, upload a corrected file, finish it with pages back, save and reopen it ([Help requests](../super-admins/help-requests.md)) | |
| **Queues** | See every queue and what's in it; retry, cancel, stop, restart or remove an item; send a large document next ([Queues](../super-admins/queues.md)) | Pausing and resuming a whole queue |
| **System** | **Now** (with the pods), **Over time**, **Versions** and the audit export's status ([System](../super-admins/system.md)) | **Open Grafana** |
| **Reports** | Every report and column, for every group, dollar costs included; the audit log; the fixed CSV exports; scheduled report views and the **Service summary** email ([Reports and audit](../super-admins/reports-and-audit.md)) | The AI gateway's admin UI |
| **Settings** | | Branding, help and messages, email, uploads and limits, sign-in and data, security alerts |
| **Services and AI levels** | | Making, changing and removing processing options and AI levels, their kinds and cost guards |

## Emails you get

As support, you get the emails super admins get about help requests: a new request, a document that couldn't be
processed, and replies on a request you've taken (or on any request while nobody has). You also get the emails asking
for approval in a group that has no admins of its own.

You also get the **service's alerts**, such as work that's stuck or email that isn't going out, and a note when each
clears. Super admins always get these at once. You choose how they come in your **Settings**, under **Service
alerts**: as they happen (the default), in one email a day, or not at all. **Help requests** and **Admin notices**
work the same way ([email and settings](../people/email-and-settings.md)). Security alerts go to super admins only.

## What the service records

Your work is recorded in the audit log, with your name, as a super admin's is: each change, and each file you open
that isn't yours. A change you make in a group is marked as made from outside it, so the group's admins can tell.
The service also records, once per sign-in and kind of page, that you looked across groups.
