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

Tabs:

| Tab | Settings |
|---|---|
| **General** | Name, description (shown at upload), switched on or off |
| **Data and notice** | Whether it sends documents to an **external provider** (blocked for local-only groups); an optional **notice** shown when people pick it, with your text |
| **Pages and cost guards** | Whether **page limits apply** (otherwise use is only counted); **most pages per document**; **most pages sent per day** (extra documents wait, they don't fail); **confirm before sending**; an optional **global pool** shared by all groups |
| **Model** (AI levels) | The gateway model, the review model, and switches (below) |
| **Kinds of document** (AI levels) | Kinds offered at upload (below) |
| **Groups** | Which groups use it, who has it there, and their pages |

A new or newly switched-on service still has to be given to groups: **+ Offer a service to a group**, or the
group's **Services and pages** tab.

## AI levels

An AI level is a model behind the AI gateway, published under a name people understand ("Local AI",
"Commercial AI"). People pick a level by name and never see the model.

**Add an AI level** publishes one of the gateway's models (the list comes from the gateway's configuration). New
levels start switched off. On its **Model** tab:

- **Gateway model:** the writer (alt text, tables, reading order, notation, OCR corrections).
- **Review model:** the AI that checks the writer's work. Use a model from a **different family**: a model tends to
  approve its own mistakes. Empty uses the engine's default.
- **Allow "review every figure":** lets people ask for every figure to be reviewed (more AI calls).
- **Read specialist notation with AI:** math, chemistry, dosage and engineering notation the layout rules can't
  read. Each reading is flagged for review.
- **Correct OCR with AI:** on scanned pages, the AI fixes words the OCR wasn't sure of. Each change is flagged.

On its **Pages and cost guards** tab, **Audits** sets what **Check only** uses with this level:

- **Use AI, free** (the default): the AI review runs and no pages are counted.
- **Use AI and count its pages:** each page counts against this level's pages (not the processing pages).
- **No AI:** checks are made without AI and use no pages.

### Kinds of document

On an AI level's **Kinds of document** tab, **add kinds** such as *Math and science* or *Forms*: a name and
description (shown at upload), with the gateway model and review model that suit that kind. *General* is the
level's own models. Switch a kind off to stop offering it.

People choose a kind at upload, or a group can set one for everyone. If a kind's model isn't available when a
document is processed, the level's General models are used and the document's page says so.

**Let AI decide** picks the kind for each document from its first pages' text. It's switched on in
[Settings → Uploads and limits](settings.md#uploads-and-limits).

## External services and local-only groups

A service marked **external** sends documents to an outside provider (a cloud AI, Adobe). For each:

- groups set to **local-only** can't use it at all;
- you can show a **notice** when it's picked, saying where documents go;
- groups can choose to mark it **External** on their upload form.

Check each provider's data-retention terms before enabling it for groups handling sensitive records.

## Model names

Model names appear only to super admins (here, and in the cost report). Everyone else sees the level and the kind.
