# People and approval

[Guide for group admins](README.md) › People and approval

People join your group automatically when they first sign in with an email address on one of the group's
domains. The group's **People** tab is where you manage them.

## Approving new members

If **New members need approval** is on (it is for new groups; see [Group settings](group-settings.md)), people
who sign in for the first time can't submit anything until an admin approves them.

1. **My group → People.** At the top, **Waiting for approval** lists them, with when they first signed in.
   Attention and a badge on Administration also show how many are waiting, and you can get an email (Admin
   notices, in your [Settings](../people/email-and-settings.md)).
2. **Approve:** they're emailed and can start straight away.
3. **Decline…:** add an optional reason, which they're shown, then **Decline**. They're emailed too. You can still
   approve them later.

## Everyone's access

> **Changed 22 October 2026:** corrected: a person's limit and access are set in the list itself; **Manage** (now the
> admin area's standard dialog) has the access check, **Describe with AI** and the account.

Pick a service at the top of the **People** tab to see each person's access to it: whether they have it, their own
limit (empty means the group's default), their use and what's left. The list comes a page at a time: search by
email or name, show only active, disabled or declined people or the group's admins, and under **Filters** only those
who have, or haven't, the service. People waiting for approval are listed on their own above it.

Each row has:

- **Has it:** a tick box, when the service is for chosen people only ("Yes (everyone)" when everyone has it);
- **Limit:** their own limit for this service; type a number and **Save** (empty uses the group's default);
- **Used** and **Left**.

**Manage** opens a dialog for one person:

- whether they can use the service, and if not, why (the same check as on Attention);
- **Describe with AI** for them: **Follow the group**, **On** or **Off** ([Group settings](group-settings.md#describe-with-ai));
- **Account:** **Disable account** or **Enable account**.

Each choice in it takes effect at once; close it with **×** or Escape.

## Personal limits

> **Changed 22 October 2026:** the steps now say where the limit is: the **Limit** column, not **Manage**.

Each person can use a service up to a **limit**, so one person can't use the whole pool:

- The **group default** applies to everyone without their own (set in [Services and pages](services-and-pages.md)).
- Give someone their **own limit** in the **Limit** column (then **Save**): higher for a heavy user, lower or zero
  to stop them using a service without disabling them.
- A limit is a ceiling, not an allowance: it can't give anyone pages the group's pool doesn't have. Limits can add
  up to more than the pool.
- Leave it empty to go back to the default. Some services count use but have no limits ("No limit").

## Disabling an account

**Manage → Disable account** stops the person signing in and ends their sessions. Their documents and records
stay. Enable it again the same way. (Removing someone from the group, or moving them to another group, is done by
a super admin.)

## Who can be a group admin

Super admins make people group admins and remove them. A group can have several admins, all with the same rights.
