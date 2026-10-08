# Verification

[Architecture](README.md) › Verification

Every output is checked after remediation, and after every edit, in three layers. Results appear under
`reports.tag_verification`, in the document's **Needs review** list, and at the top of the reports.

| Layer | Reads the PDF with | Checks | Effect |
|---|---|---|---|
| 1. Integrity | pikepdf + its own content-stream scan (no shared code with the writer) | Every tagged sequence belongs to exactly one tag; ParentTree and annotation links are consistent; no nested or unbalanced marked content; roles are standard; nesting | **Fails the job (or the save)** |
| 2. Semantics | pdfplumber (independent of PyMuPDF) | Headings that look like body text, figures over nothing, file-name alt text, hidden text, table text, list numbering, link text, reading order against an independent ordering, language | Review list |
| 3. AI review | Rendered images + a review model from a different family | Alt text accurate, partly wrong or wrong (with a suggested rewrite); checklists for charts, formulas, handwriting, sheet music | Review list |

## Layer 1: integrity

`app/verification/integrity.py` reads the finished PDF back with its own tree walk and content-stream scan, sharing no
code with the tagger, and checks that:

- every tagged content sequence belongs to exactly one tag, and every tag points at content that exists;
- the ParentTree, `StructParents` and annotation `StructParent` entries are consistent;
- parent pointers are correct and there are no cycles;
- every role resolves to a standard structure type;
- marked content is balanced and not nested;
- PDF 2.0 nesting rules hold (reported as warnings).

**Any integrity error fails the job** (`error: tag_integrity_failed`, with the failures listed); the outputs are kept
for inspection. Tests break good files in ten specific ways and assert each defect is caught.

## Layer 2: semantics

`app/verification/semantic.py` reads page content through pdfplumber, a parser independent of PyMuPDF, and compares
each tag with what it covers:

- headings that look like body text, and large short paragraphs that look like missed headings;
- figures over no visible content, alt text that's a file name, and one alt text shared by different images;
- large images or body text hidden as artifacts;
- table cells that don't match the table on the page, and data tables without header cells;
- single-item lists and non-sequential numbering;
- generic link text;
- tag order that disagrees with an independent layout ordering;
- passages in another language than their tag.

These findings never block a job.

## Layer 3: AI review

`app/verification/ai_review.py`. A **review model**, from a different family than the writer, checks alt text against
the image, with a checklist per content type: charts must state the trend and correct values, formulas must read
correctly in words. It reviews everything Layer 2 flagged plus a reproducible 10% sample; the `full_review` flag
("Review every figure") reviews every figure and formula. Its findings are review items with a suggested alt text,
never automatic fixes.

The reviewer can also look at a page with numbered boxes drawn in tag order, to judge reading order. This is **off by
default** (`VERIFY_AI_READING_ORDER=false`): in testing, two local models called correctly ordered pages wrong most of
the time, while the Layer 2 check judged them correctly. Enable it only with a reviewer validated on labeled pages.

When a document has two output files, a figure or formula that appears in both is reviewed once.

Settings: `VERIFY_*` in `.env.example`; `VERIFY_ROUTES` sends specific content types to specific reviewers. See
[Operations → AI models](../operations/ai-models.md#verification-settings).

## A pass is not full accessibility

veraPDF checks the machine-verifiable rules of PDF/UA-1. It can't tell whether alt text is accurate, whether headings
are the right ones, or whether the reading order makes sense. That's why Layers 2 and 3 and the review list exist. The
same applies to the output of any other remediation tool.
