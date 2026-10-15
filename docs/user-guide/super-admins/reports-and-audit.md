# Reports and audit

[Guide for super admins](README.md) › Reports and audit

**Administration → Reports** works as for group admins ([how to use the reports](../group-admins/reports.md)), with
two differences: you pick **every group or one**, and you get more reports and columns.

## Reports only super admins see

| Report | What it shows |
|---|---|
| **Cost** | AI spend and Adobe pages, by group, service and AI level, over time (with a chart) |
| **Daily use** | Pages sent per day for each service, against its daily limit (with a chart) |
| **Report emails** | The scheduled email reports sent: who, which, when |

The **Usage** group is shown as **Usage and cost** for you. Cost columns (AI spend per document) appear in other
reports too. What groups are charged is pages, not money, so cost is for super admins only.

**AI usage** (also in the Usage group) shows you more than group admins see: a row per **model** as well as per AI
level and kind of document, the **AI cost** in total, per document and per page, and (in **Columns**) the review
model, the review's cost and tokens, the cost of **Let AI decide**, and how often a kind's model wasn't available and
the level's General models were used. Use it to compare levels and models on real documents.

Documents sent without an AI level use the engine's own default AI (its `AI_PROVIDER` setting): they're counted on
a row of their own, **Engine default AI (no AI level)**. If most documents land there, the groups probably aren't
offered an AI level, or have none as their default (Group → Services).

Tokens, failed calls and models are recorded from this version on: older documents count in documents, calls and
cost, but not in tokens (**Documents with token counts**, in Columns, says how many do).

The original fixed-format CSV exports are still available, under **Fixed-format CSV exports**, for scripts that rely
on them.

## The audit log

The **Audit log** report records every change: to pages, limits, grants and services; sign-ins and code requests;
uploads and downloads of other people's files; settings; queues; help requests; tag edits; report exports. Each event
has who, when, from where, the group, and the details.

Group admins see their own group's part, including changes super admins made there. Records are kept for the
**records** retention period set in Settings → Sign-in and data.

## The AI gateway

All AI calls go through the AI gateway (LiteLLM), which has its own admin UI: models with a playground, spend per
model and per job, request logs and errors, and virtual keys with budgets. When it's configured, a link appears in the
Administration menu and on **Services and AI levels**. See [Operations → AI models](../../operations/ai-models.md#the-gateway).

## Your own email summary

**Settings → Email reports → Service summary** (top right, your personal settings): pages by group and option, AI
cost and Adobe pages, days a daily limit was reached, quality and the most common remaining failures, and the help
queue, monthly, weekly or daily.
