# Services and AI levels

[Guide for super admins](README.md) › Services and AI levels

**Administration → Services and AI levels** has two tables: **processing services** (how documents are tagged)
and **AI levels** (which AI helps). Each shows where documents go, pages and today's use; each row opens the
service's own page.

## Processing services

- **Standard:** this service's own engine, on its own servers. Always available.
- **Adobe Autotag:** sends documents to Adobe PDF Services. Seeded switched off, marked external, with a notice and
  confirm-before-send on. It needs the engine's Adobe credentials and `ADOBE_ENABLED=true` first; see
  [Operations → Adobe Autotag](../../operations/adobe-autotag.md).

## A service's page

> **Changed 22 October 2026:** the table lists Adobe Autotag's **Adobe connection** tab, and **Add an AI level** and
> **Add a kind of document** open in the admin area's standard dialog.

Tabs:

| Tab | Settings |
|---|---|
| **General** | Name, description (shown at upload), switched on or off |
| **Data and notice** | Whether it sends documents to an **external provider** (blocked for local-only groups); an optional **notice** shown when people pick it, with your text |
| **Pages and cost guards** | Whether **page limits apply** (otherwise use is only counted); **most pages per document**; **most pages sent per day** (extra documents wait, they don't fail); **most documents at once** (for what can't grow with the rest, such as a local AI's GPUs: extra documents wait their turn); **confirm before sending**; an optional **global pool** shared by all groups |
| **Model** (AI levels) | The gateway model, the review model, and switches (below) |
| **Adobe connection** (Adobe Autotag) | A note that the engine connects to Adobe with its own credentials and rate limits; use the daily limit and most pages per document to control Adobe spend |
| **Kinds of document** (AI levels) | Kinds offered at upload (below) |
| **Groups** | Which groups use it, who has it there, and their pages |

A new or newly switched-on service still has to be given to groups: **+ Offer a service to a group**, or the
group's **Services and pages** tab.

## AI levels

An AI level is a model behind the AI gateway, published under a name people understand ("Local AI",
"Commercial AI"). People pick a level by name and never see the model.

**Add an AI level** opens a dialog that publishes one of the gateway's models (the list comes from the gateway's configuration). New
levels start switched off. On its **Model** tab:

- **Gateway model:** the writer (alt text, tables, reading order, notation, OCR corrections), chosen from the
  models the gateway serves. A level whose model was renamed or removed at the gateway shows it as **not served
  by the gateway**: choose another and save.
- **Review model:** the AI that checks the writer's work. Use a model from a **different family**: a model tends to
  approve its own mistakes. **Default review model** uses the engine's default.
- **Allow "review every figure":** lets people ask for every figure to be reviewed (more AI calls).
- **Read specialist notation with AI:** math, chemistry, dosage and engineering notation the layout rules can't
  read. Each reading is flagged for review.
- **Correct OCR with AI:** on scanned pages, the AI fixes words the OCR wasn't sure of. Each change is flagged.

On its **Pages and cost guards** tab, **Audits** sets what **Check only** uses with this level:

- **Use AI, free** (the default): the AI writes alt text and reads tables, reading order and notation for the
  report, as in a remediation, and no pages are counted. Nothing limits what checks cost you, so prefer one of the
  other two for a paid level.
- **Use AI and count its pages:** each page counts against this level's pages (not the processing pages).
- **No AI:** checks are made without AI and use no pages.

### Deleting an AI level

An AI level that is no longer offered can be deleted from its **General** tab. Switch it off and save first; it
can be deleted once no document is waiting or in progress with it. Its kinds of document go with it, and so do
every group's and person's settings, pages and page counts for it. Documents already processed keep their record,
and the page ledger and the activity log keep their rows; reports and the documents themselves then show the
level by its key (for example `ai:large-ai`) instead of its name. A later AI review of one of those documents
uses the engine's default review model. Deleting can't be undone: to stop offering a level for a while, switch it
off instead. Standard and Adobe Autotag can't be deleted.

### Kinds of document

On an AI level's **Kinds of document** tab, **Add a kind of document** adds kinds such as *Math and science* or *Forms*: a name and
description (shown at upload), with the gateway model and review model that suit that kind. *General* is the
level's own models. Switch a kind off to stop offering it.

People choose a kind at upload, or a group can set one for everyone. If a kind's model isn't available when a
document is processed, the level's General models are used and the document's page says so.

**Let AI decide** picks the kind for each document from its first pages' text. It's switched on in
[Settings → Uploads and limits](settings.md#uploads-and-limits), on the **AI** sub-tab.

## External services and local-only groups

A service marked **external** sends documents to an outside provider (a cloud AI, Adobe). For each:

- groups set to **local-only** can't use it at all;
- you can show a **notice** when it's picked, saying where documents go;
- groups can choose to mark it **External** on their upload form.

Check each provider's data-retention terms before enabling it for groups handling sensitive records.

## Model names

Model names appear only to super admins (here, and in the cost report). Everyone else sees the level and the kind.
