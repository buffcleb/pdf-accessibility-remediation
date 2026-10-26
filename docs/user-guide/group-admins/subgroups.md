# Subgroups

[Guide for group admins](README.md) › Subgroups

> **New 26 October 2026** (#431).

A **subgroup** is a group inside yours: a department within a faculty, say, or a team within a department. Its
people use your group's pages and follow your group's settings, but it has its own list of people, documents,
help requests and reports, and it can have admins of its own. Subgroups go one level deep: a subgroup can't have
subgroups.

As an admin of the parent group you manage its subgroups as well. A subgroup's own admins manage only their
subgroup: they don't see the parent's people, documents or reports, or another subgroup's.

## Adding a subgroup

**Administration → My group → Add a subgroup** (beside the group's name), give it a name, then **Add subgroup**. Super
admins and service admins can add one too. The new subgroup:

- starts with your group's settings (it follows them, below) and with the same services switched on, for the same
  people, with the same default limits;
- has no email domains or addresses of its own until a super admin adds them (on its **Group settings** tab);
- has no admins of its own until a super admin or service admin adds one. Until then it's yours to run.

The groups list shows each subgroup under its parent. On a group's page, **Group or subgroup** switches between the
parent and its subgroups, staying on the same tab.

## Who joins it

People join a subgroup when they first sign in, the same way as for any group:

1. an address listed on the subgroup comes first;
2. then the subgroup's own email domain, such as `nursing.example.edu`;
3. then everyone else at the parent's domain joins the parent.

Each address and domain is listed on one group only.

You can also move someone between your group and its subgroups: **People → Manage → Group**. It takes effect at
once. Their documents and past use stay where they were, and if they were an admin of their group they stop being
one. To move someone outside your group and its subgroups, ask a super admin.

A subgroup's own admins can't move people out to the parent: that takes an admin of both.

## Pages

A subgroup has no pages of its own. Its documents use the parent's pool, so the parent's **Services and pages**
shows everything its subgroups use, and their documents are refused when the parent's pool runs out.

To keep one subgroup from using everything, give it a **limit**: on the subgroup's **Services and pages** tab, **Set
limit** for a service. Only the parent's admins (and super admins and service admins) can set it; the subgroup's own
admins see it but can't change it. An empty limit means whatever the parent has left.

When a document is refused, the message says which ran out: the parent's pool ("Campus A, whose pages your group
uses, has 0 Standard pages left") or the subgroup's limit ("Your group has 4 Standard pages left of its limit").
People's own limits work as in any group.

Pages are added to the parent, never to a subgroup. When a subgroup's limit runs low, its admins and the parent's
admins are emailed, as for a pool running low.

## Settings

A subgroup follows its parent for:

- AI for uploads (who picks the AI level, and the group's level and kind);
- Local only;
- New members need approval;
- Describe with AI;
- Show which options are external;
- the over-quota message.

When you change one of these on the parent, its subgroups that follow it change too. On a subgroup's **Group
settings** tab, each of these says whether it follows the parent or is set for the subgroup. Changing it there makes
it the subgroup's own, and the parent's changes no longer reach it. **Settings from** the parent, at the top of
the tab, lists the subgroup's own settings, with **Use** the parent's to follow the parent again.

Local only is a data rule, so a subgroup can't loosen it. While the parent is local-only, so is every subgroup, and
they can't turn it off. When the parent turns it off, the subgroups that follow it turn it off too. A subgroup that
turned local-only on for itself keeps it.

If the parent uses one AI level for everything and a subgroup doesn't have that level switched on, the subgroup's
**AI for uploads** says so: its uploads that need the level are refused until it's switched on.

## Reports

A parent's reports, lists and counts include its subgroups': its documents, people, help requests, Attention and
email summary. The **Group** column shows whose each row is, and the **Group** filter narrows a report to the parent
or one subgroup. A subgroup's admins see only their subgroup's.
