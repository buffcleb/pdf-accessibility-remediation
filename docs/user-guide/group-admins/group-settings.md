# Group settings

[Guide for group admins](README.md) › Group settings

**My group → Group settings.** Each change takes effect at once and is recorded in the audit log.

## New members

**New members need approval** (on for new groups): people who sign in for the first time wait for an admin
before they can submit anything (see [Approving new members](people-and-approval.md#approving-new-members)).
Turn it off if everyone on your group's email domains may use the service straight away. Turning it off doesn't
approve people already waiting; the page links to them.

## AI for uploads

Who picks the AI level and kind of document:

- **People choose** (the default): for each upload, and for single files. The group's preselected level is chosen
  to start with.
- **One level for everything:** people aren't asked. Every document uses the **AI level** and **kind of document**
  you set here. Use this when your group should always use the same AI, for cost or data reasons.

The kind can be *General*, a specific kind such as *Math and science*, or **Let AI decide** if the service offers
it, which picks the kind for each document from its text.

## Where documents can go

### Local only

**Local only** keeps your group's documents on the service's own servers: every service that sends documents to
an outside provider (cloud AI, Adobe, an outside classifier) is blocked, whatever else is switched on. Use it for
groups handling personal, student, patient or personnel records.

### Show which options are external

Marks the processing options and AI levels that send documents to an outside provider with **External** on the
upload form. It's off by default. Turn it on if people in your group should see where their documents go.
Independently, super admins can show a notice when a particular option is picked.

## Managed by super admins

The group's name, email domains, group admins and corrections to its pool are edited by super admins (on the same
tab, for them).
