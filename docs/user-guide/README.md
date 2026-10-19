# User guide

How to use the service, by role. Each guide is written for someone who uses the service a few times a year,
so it starts from the beginning each time and says what you'll see at each step.

| You are… | You… | Start here |
|---|---|---|
| **Someone who needs accessible PDFs** | upload documents, download the results, check and fix what the machine couldn't be sure about, and ask for help | [Guide for people submitting documents](people/README.md) |
| **A group admin** | look after one group (a department, team or organization): approve people, share out pages, set the group's choices and follow its reports | [Guide for group admins](group-admins/README.md) |
| **A super admin** | run the service: set it up, offer services and AI levels to groups, add pages, handle help requests and watch the queues | [Guide for super admins](super-admins/README.md) |

Admins are also ordinary users, so the first guide applies to everyone. The [glossary](glossary.md) explains the
terms used throughout (PDF/UA, tags, alt text, AI level and so on).

## In one paragraph

You upload a PDF. The service adds the hidden structure that screen readers and other assistive technology need
(headings, paragraphs, lists, tables, figures with descriptions, reading order, language and title), aiming at
**PDF/UA-1**, the standard behind WCAG and Section 508 conformance for PDFs (**PDF/UA-2** for a PDF 2.0 file, which
can't be PDF/UA-1). It checks the result with veraPDF,
the reference checker, and with its own checks, and gives you a list of what a person should look at. It never
changes how your document looks, unless that's the only way to make it pass; then you get two files and choose.

## Other documentation

- [Documentation home](../README.md): everything else, from architecture to deployment.
- For integrations and testing, the engine's API: [API reference](../api/README.md).
