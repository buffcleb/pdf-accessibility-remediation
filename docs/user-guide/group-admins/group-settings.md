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

### No AI

> **New 29 October 2026** (#659)

For documents that mustn't go to any AI, such as sensitive records. Once super admins offer it, **No AI as a choice**
can be:

- **Not offered** (the default).
- **Offered beside the AI levels:** people can pick **No AI** at upload, like a level.
- **Offered and picked to start with:** **No AI** is preselected, ahead of the group's default level; people can
  still pick a level.

To make No AI the **only** choice, pick **One level for everything** and choose **No AI** as the level.

A document made with No AI is sent to no AI at all, for any step: its pages count against the processing service only,
never an AI level's. Figures are tagged without alt text, and each is listed under **To review** for someone to
describe in the tag manager, so the document doesn't pass PDF/UA until they are. Tables, headings and reading order
come from the layout rules alone. Afterwards the document isn't sent to AI either: its saves get no AI review, and
**Describe with AI** and **Suggest tags with AI** aren't offered for it. A document with no AI level (your group has
none switched on, or none was picked and there's no default) is made the same way.

Subgroups take this setting from their parent with the rest of **AI for uploads**.

## Where documents can go

### Local only

**Local only** keeps your group's documents on the service's own servers: every service that sends documents to
an outside provider (cloud AI, Adobe, an outside classifier) is blocked, whatever else is switched on. Use it for
groups handling personal, student, patient or personnel records. **New 29 October 2026:** a local-only group can
also offer [No AI](#no-ai), even when all its AI levels are external.

### Show which options are external

Marks the processing options and AI levels that send documents to an outside provider with **External** on the
upload form. It's off by default. Turn it on if people in your group should see where their documents go.
Independently, super admins can show a notice when a particular option is picked.

## Describe with AI

> **Changed 22 October 2026:** one person's setting is in the **Manage** dialog on their row under **People**.

In the tag manager's **Split the figure** window, **Describe with AI** writes the alt text for the part someone picked
or boxed. The same setting gives people **Suggest tags with AI** in **Areas**, where the AI suggests the tags for an
area of the page (**New 27 October 2026**). Each is one AI call at the document's AI level each time, recorded on the document's cost. Choose whether your
group's people have it: **Follow the service default** (whatever the super admins set), **On for the group** or
**Off for the group**. For one person, choose **Manage** on their row under **People** and set **Describe with AI**
to **Follow the group**, **On** or **Off**. The person's setting wins over the group's, and the group's over the service default.

## API tokens

**Let people make API tokens** allows the group's people to make tokens for scripts that send documents (see the
people's guide, *Email and settings*). Each token sends to one processing option and AI level the person may use,
and goes through the same pages and limits as the web app. Super admins switch tokens on for the service first.
Switching this off stops every token in the group at once.

**The group's API tokens**, under the switch, lists your people's tokens: whose, what each sends to and may do, when
it expires and was last used (never the token itself). **Revoke** stops one at once, and it's recorded in the audit
log. Documents sent by a script are marked **sent by script** on their page, and the group's **Documents** can be
filtered to them.

## PDF/UA-2

**New 24 October 2026** (#445). Once super admins offer PDF/UA-2 for the service, **Let people choose PDF/UA-2**
puts an **Accessibility standard** choice on your people's upload form: PDF/UA-1, PDF/UA-2 (the file delivered as
PDF 2.0) or Both (a PDF/UA-1 file and a PDF/UA-2 copy, from one run, for the same pages). **Preselected standard**
is what the form starts with, and what a script's documents get when it doesn't say. Off, or while the service
doesn't offer it, everyone gets PDF/UA-1.

PDF/UA-1 is still what most policies and checkers mean. PDF/UA-2 is newer and needs PDF 2.0, which current Adobe
Acrobat and PAC 2024 read but some older readers and checkers don't yet: Both is the safe choice while you find out.

## Managed by super admins and service admins

> **Changed 23 October 2026:** service admins too (#564).

The group's name, email domains, group admins and corrections to its pool are edited by super admins and service
admins (on the same tab, for them).
