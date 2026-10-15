# Groups and people

[Guide for super admins](README.md) › Groups and people

## Groups

A **group** is who a service is shared with: a department, organization or team. Everyone belongs to exactly one,
decided at their first sign-in by their email address.

![Administration, Groups: a search box, a table with Northfield University, its email domain, the services on (Standard, Commercial AI) and its pages left, and Create a group.](../../images/admin-groups.png)

**Groups** lists them all; **Create group** makes a new one. A group's own tabs are the same as a group admin's (see
[the admin area](../group-admins/the-admin-area.md#your-groups-pages)), plus what only super admins can change, on
**Group settings**:

- **Name**, the **default page limit per person**, and the group's own **message when out of pages** (empty uses the
  global message).
- **Email domains:** everyone signing in with an address on one of these domains joins the group. A domain belongs
  to one group only. A subdomain only matches if it's listed. Adding a public email provider (gmail.com,
  outlook.com and so on) asks you to confirm, because anyone with a free account could then join.
- **People allowed by address:** individual email addresses that may sign in and join this group, without their
  domain. Use it for someone with a gmail.com address, a visitor or a contractor. An address here takes precedence
  over the domains, so it can also put one person from partner.edu in a different group from the rest of
  partner.edu. An address is on one group at a time (adding it to another offers to move it). Removing it stops new
  sign-ups only: if they've already signed in, their account keeps working until you disable it in People.
- **Group admins:** add or remove them. A group can have several.
- **Disable group:** its people are signed out and can't sign in until it's enabled again.
- **Correct the pool** (below).

### A group for people without an organization domain

For people who don't share an organization's email domain (individuals with personal addresses, guests), create a
group such as **Individuals** with no email domains, and add each person under **People allowed by address**. It
works like any other group: its own services, page pool, limits and admins.

## Pages and top-ups

Each service has a **pool** per group, and optionally a **global pool** shared by every group (set on the service's
page). Pages are added with a **top-up**:

1. The group's **Services and pages** tab → **Add pages** on the service's row.
2. Enter the number of pages and a **ticket number** (and optionally a link and comment), for your records.
3. No ticket? Tick **override** and give a comment. Overrides are flagged in the ledger and reports.

Every top-up records who made it, when, from where, and the balances before and after. **Correct the pool** adds or
removes pages (with a reason) to fix mistakes.

**Reserve, then commit.** When a document is accepted, its pages are reserved on every service it uses, at every
level (global, group, person) at once. They're committed when it's done, and given back if it fails, is canceled,
or the AI it was charged for turned out not to be needed.

## People (all groups)

**People (all groups)** lists everyone, with their group and use. Search by email. **Manage** opens a panel to:

- **move** them to another group (their past use stays with the old group);
- **correct their usage:** give pages back or charge more, per service, with a reason. It's recorded in the ledger
  and the audit log. (Use this when a document didn't come out usable, for example.)
- **disable** their account (it ends their sessions; their documents stay);
- **make them a super admin**, or remove it.

Personal limits are set in the group's **People** tab, as a group admin does.

## Removing documents

To clear out documents before the end of the retention period (test uploads, for example), open the group's
**Documents** tab, tick the documents (or the heading's box for every one shown), give a **reason** (it's recorded
in the audit log), then:

- **Remove files now:** the files, report, versions and help requests about them go at once (as at the end of
  retention); the documents stay in their owners' lists, marked "Files removed".
- **Delete:** the files go at once and the documents leave every document list, their owners' included. Their
  records stay, so reports, page usage and the ledger still count them; super admins can still open them (from
  **Reports**), marked as deleted.

Documents still waiting or in progress can't be ticked. Neither can be undone. A single document has the same two
choices on its page, under **Downloads → Super admin: remove**, where you can also **Keep the files** of a document
its owner marked done.

## When a sign-in code doesn't arrive

If email is failing, the person running the servers can issue a code from the command line with
`scripts/sign-in-code.sh` (see [Operations → Email](../../operations/email-and-branding.md#when-sign-in-codes-dont-arrive)).
It's recorded in the audit log.
