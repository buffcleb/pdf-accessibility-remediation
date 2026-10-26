# Engine refactoring plan

A review of the remediation engine (`app/`, about 26,700 lines of Python in 95 files; `tests/engine`, about 130
files) made on 24 October 2026, after the fixes of that week (#597 worksheet, #598 endnotes, #603 report glyphs,
#604 title and Part 1, #606 MathML from structure elements, #607 Symbol and Wingdings). It says where the code has
become hard to change or easy to break, what to move where, and in what order. Nothing here changes a document's
output: each item is a refactor, checked by the same three comparisons (below). The user picks from the list; each
item is one draft PR.

The open engine work it is ranked against: **#445** (PDF/UA-2 as an option for PDF 1.x documents) and **#413**
(PDF/UA-2 nesting rules with kept tags). Each item says whether it is better done before or after those two.

## How to check that a refactor changed nothing

Every item is checked the same three ways, and the plan's first item builds the one tool that is missing.

1. **The engine tests:** `.venv/bin/python -m pytest -q tests/engine` (from a checkout that has the `.venv`; a
   fresh worktree has none).
2. **The role comparison** (item 0): every block's role and text on every page of the 32 regression documents,
   before and after, from `app.pipeline.orchestrator.prepare_analysis(Path)` (the analysis without AI). A refactor
   of the analysis or structure code must produce an identical listing; a refactor of the tagging code leaves the
   listing alone by construction, and is checked by the tag maps instead (the viewer's `*_tagmap_*.json` written by
   the regression sets, compared file by file).
3. **The regression sets:** `REGRESSION_SAMPLES=/Users/bordelcl/Projects/BuffClebPDFRemediation/samples/regression
   scripts/regression.sh --quick --every 0`, then `--full`, with `REGRESSION_IMAGE` set to a pulled engine image.
   The documents live only in the main checkout (`samples/regression/`, git-ignored, described in its `NOTES.md`),
   and are confidential: a comparison reports counts and ids, never their text.

Where an item says "role comparison", it means (2); "tag maps", the tag-map half of (2); "sets", (3).

## The ranked list

| Rank | Item | Size | Before or after #445/#413 |
|---|---|---|---|
| 0 | [A role and tag-map comparison tool](#0-a-role-and-tag-map-comparison-tool) | ~150 new lines | before everything else |
| 1 | [One conformance target for the PDF/UA part](#1-one-conformance-target-for-the-pdfua-part) | ~250 | before #445 (it is most of #445's groundwork) |
| 2 | [Shared tree helpers for the tagging modules](#2-shared-tree-helpers-for-the-tagging-modules) | ~300 | before #413 |
| 3 | [`tag_document` in phases; the kept route in its own module](#3-tag_document-in-phases-the-kept-route-in-its-own-module) | ~700 moved | before #413 |
| 4 | [`structure.py` as a package of passes with an explicit order](#4-structurepy-as-a-package-of-passes-with-an-explicit-order) | ~950 moved, ~80 new | after #445/#413 (independent of them) |
| 5 | [Named block-split rules in `_extract_blocks`](#5-named-block-split-rules-in-_extract_blocks) | ~150 | after |
| 6 | [Shared marker regexes](#6-shared-marker-regexes) | ~120 | with item 4, or on its own |
| 7 | [The run's result built in one place](#7-the-runs-result-built-in-one-place) | ~400 | after; independent |
| 8 | [`run_remediation` as named stages](#8-run_remediation-as-named-stages) | ~500 moved | after item 7 |
| 9 | [`editor.py` as a package, with a dispatch table](#9-editorpy-as-a-package-with-a-dispatch-table) | ~2,500 moved, ~100 new | any time; touches nothing #445/#413 touch |
| 10 | [Test fixtures for pages and blocks](#10-test-fixtures-for-pages-and-blocks) | ~200 | any time |
| 11 | [Dead code, duplicate helpers, doubled tests](#11-dead-code-duplicate-helpers-doubled-tests) | ~100 removed | any time |

The order is "what makes the next engine changes easiest": items 1-3 take apart the code #445 and #413 will have to
change; items 4-6 take apart the code every layout fix lands in; 7-9 are maintenance of large files that are not in
the path of the open issues; 10-11 are housekeeping.

---

## 0. A role and tag-map comparison tool

**Problem.** There is no way today to say "this change altered no document's roles" except by reading veraPDF and
PAC results, which pass or fail on compliance, not on sameness. The regression sets (`scripts/regression.sh`,
`harness/run_folder.py`) check PDF/UA and write tag maps, but nothing diffs the analysis itself. Every item below
needs that diff.

**Change.** A script, `harness/roles_dump.py`, that runs `prepare_analysis` on every PDF under a folder and writes
one JSON file per document: for each page, `columns`, `reading_order`, and for each block `id, role, list_label,
note_mark, note_kind, continues, artifact_type, bbox` (rounded to a tenth of a point) and a hash of `text` and of
`actual_text` (a hash, so the dump holds none of the confidential text). A second mode diffs two dumps and prints,
per document, the ids whose role or text changed. The same script, given two output folders of a regression run,
diffs the `*_tagmap_*.json` files by element path, role and text hash. It belongs in `harness/`, beside
`run_folder.py`, and is documented in `harness/README.md` and `samples/regression/NOTES.md` (the latter in the main
checkout only).

**Risk.** None to the engine: it reads. The one thing to get right is that block ids are stable across runs (they
are: `analyzer.py:863-864` numbers them by position after merging).

**Check.** Run it twice on the same commit; the diff must be empty.

**Size.** ~150 new lines. Depends on nothing. Everything else depends on it.

---

## 1. One conformance target for the PDF/UA part

**Problem.** Which PDF/UA part a file is tagged, labelled and judged by is decided in eight places, and three of
them make the decision independently:

- `app/config.py:61-64, 284-289`: `pdfua_part` must be 1 (the validator refuses 2).
- `app/tagging/tagger.py:607`: `part = 2 if str(pdf.pdf_version) >= "2.0" else pdfua_part`; `:608`
  `_document_in_pdf2_namespace` (the only place a PDF 2.0 namespace is written, and only on the Document);
  `:609` passes the part to `apply_document_metadata`.
- `app/tagging/metadata.py:60-65`: writes `pdfuaid:part`, and `rev` 2024 when the part is 2.
- `app/pipeline/adobe_job.py:317-321`: its own copy of the version rule, ignoring the setting.
- `app/validation/verapdf.py:118-131` (`pdfua_part(path)`) and `:134-142` (`flavours`), used at
  `orchestrator.py:179` and `:200`; `orchestrator.py:321-325` (`_rules`) always repairs by the UA-1 result.
- `app/tagging/mathml_embed.py:56`: MathML attached only in a PDF 2.0 file.
- `app/reports/pdf.py:90-91` and `app/reports/markdown.py:134` read the part back from the snapshot.

None of the tree builders takes a part: `StructTreeBuilder` (`struct_tree.py`), `ExistingTree` (`existing_tags.py`)
and `structure_rules.apply` all assume PDF/UA-1 nesting and the PDF 1.7 role set (`existing_tags.py:24-41`:
`STANDARD_TYPES` has no PDF 2.0 types; `KNOWN_ROLE_MAP` maps FENote to Note, Aside to Div, Title to P, Em/Strong/Sub
to Span, and that map is written into the file's RoleMap at `:1104-1108`). `/Ref`, a PDF 2.0 key, is written
whatever the version (`struct_tree.py:143, 149, 159`; `tagger.py:997`). #445 has to make the part a per-job choice
and #413 has to make the nesting rules depend on it; today both would mean finding every one of these places again.

**Change.** A small frozen dataclass, `app/tagging/conformance.py`:

```python
@dataclass(frozen=True)
class Conformance:
    part: int            # 1 or 2: the PDF/UA part the file is tagged and labelled by
    pdf_version: str     # the file's own
    namespaces: bool     # PDF 2.0 namespaces are written (part 2 in a PDF 2.0 file; #445 decides for 1.x)
    mathml: bool         # MathML may be attached (today: PDF 2.0 only)

def for_file(pdf: pikepdf.Pdf, requested_part: int) -> Conformance: ...
```

`for_file` holds the one rule that `tagger.py:607`, `adobe_job.py:318` and `verapdf.py:131` each have now. It is
made once per `tag_document` call and passed to `StructTreeBuilder`, `ExistingTree`, `structure_rules.apply`,
`apply_document_metadata` and `mathml_embed.embed`, each of which reads `conf.part` instead of deciding. `verapdf.
flavours` takes a `Conformance` (or its part). `_rules` in the orchestrator keeps its UA-1 preference but says so
through the object. The report readers (`reports/pdf.py`, `reports/markdown.py`) stay as they are: they read the
snapshot.

`#445` then becomes: `JobOptions` gets a `pdfua_part`, `for_file` honours it for a 1.x file, and `structure_rules`
and `existing_tags` consult `conf.part` where they choose a role set; `#413` adds the part-2 branch of the nesting
rules in one module.

**Risk.** Low: the object carries the same values the code computes today; a wrong wiring shows up at once as a
changed `pdfuaid:part` or a lost namespace. The Adobe route's copy (`adobe_job.py:318`) only writes the identifier
when none is there; keep that behaviour, only take the value from `for_file`.

**Check.** Tests (`test_pdfua_part.py`, `test_mathml_embed.py`, `test_old_metadata.py`); tag maps; the quick set,
comparing each output's XMP `pdfuaid:part` and the Document element's `/NS` before and after (two numbers per
document).

**Size.** ~250 lines, across `tagger.py`, `struct_tree.py`, `existing_tags.py`, `structure_rules.py`, `metadata.py`,
`mathml_embed.py`, `verapdf.py`, `adobe_job.py`, `orchestrator.py`. Conflicts with item 3 (both touch
`tag_document`): do this one first, it is the smaller.

---

## 2. Shared tree helpers for the tagging modules

**Problem.** The same five or six small jobs on a pikepdf structure tree are written in every module that touches
one, each slightly differently:

| Job | Copies |
|---|---|
| The items of `/K` (one or an array) | `integrity._items` (`integrity.py:64-67`, imported by `editor.py:115`, `semantic.py` and `tagmap.py`); identical `_items` in `reorder.py:29`, `link_words.py:29`, `empty_spans.py:24` and `adobe/chunks.py:139`; `_kids` in `structure_rules.py:39`, `self_links.py:71` and `mathml_tags.py:72`; `ExistingTree._iter_k` (`existing_tags.py:275-281`); inline about fifteen more times (`tagger.py:675, 686`, `struct_tree.py:55, 635, 721`, `adobe_job.py:354, 361, 404`, `audit.py:126, 142`, `editor.py:745, 2303, 2342`, `tagmap.py:37`, `table_headers.py:90`) |
| "Is this a structure element" | `existing_tags._is_struct_elem` (`:131-132`); `editor._is_elem_dict` (`:1152-1153`); `integrity._is_elem` (`:70-71`); `structure_rules._is_elem` (`:35`, the only one that also checks `/Type`); inline at `tagger.py:678-679` |
| The standard role set | `existing_tags.STANDARD_TYPES` (`:24`) and `integrity.STANDARD` (`integrity.py:17`), the same set; `_TEXT_ROLES` identical at `tagger.py:920` and `adobe_job.py:530`, with smaller variants at `verify.py:42` and `mathml_embed.py:29` |
| A pre-order walk | `struct_tree._iter_elems` (`:52-63`, recursive); `tagger._tree_walk` (`:670-690`, iterative with `seen`); `editor._walk` (`:664-675`); `ExistingTree._walk` (`:283-318`, builds `ElemInfo`); `struct_tree._referenced_annots` (`:628-647`); `_parent_tree.walk` (`:694-718`) |
| RoleMap resolution | `existing_tags._role_map` + `resolve_role` (`:116-128`); `ExistingTree._role_of` (`:1101`); `editor._role_of` (`:876-886`); `integrity._walk_tree` (`:175-185`); inline at `structure_rules.py:97-99` (no Name filter, `lstrip("/")`) |
| Number-tree (ParentTree) lookup | `editor._number_tree_get` (`:1067-1082`, honours `/Limits`), `_set_number_tree` (`:1098`, ignores them) and `_page_array.collect` (`:1804-1814`); `ExistingTree._parent_tree_owner` (`:209-231`, depth 50); `IntegrityChecker._number_tree` (`integrity.py:296-304`, depth 32); `adobe/chunks._number_tree` (`:130`, no depth guard); `self_links.py:92` uses `pikepdf.NumberTree` instead |
| A new element dictionary | `StructTreeBuilder._elem` (`struct_tree.py:276-283`); `ExistingTree._new_elem` (`existing_tags.py:529-533`); `editor._new_elem` (`:2358-2362`); inline at `structure_rules.py:157-158, 209-210` |
| An MCR dictionary | `struct_tree._mcr` (`:342-346`) and inline at `:358-361`; `existing_tags.py:1047`; `link_words.py:123` |
| A unique `/ID` | `editor._give_id` (`:723-733`); `StructTreeBuilder._new_id` (`struct_tree.py:169-180`) |
| Box arithmetic | `models.py:16-28` has `bbox_area`, `bbox_intersection`, `bbox_union`; copies: `tagger._area` (`:916`) and `link_words._area` (`:35`); `_overlap` identical at `mathml_embed.py:32` and `verify.py:38`; `mathml._iou` (`:177`, hand-rolled, doesn't clamp) beside `transfer._iou` (`:12`, uses the helpers); `tagmap._union` (`:52`); inline intersection or union at `ocr.py:154`, `semantic.py:150, 824`, `reorder.py:215`, `content_marker.py:294`, `scan_figures.py:128`, `field_labels.py:163` |
| 2×3 matrix maths | `editor._mul`, `_point` (`:1933-1940`); `content_marker.mat_mul`, `apply` (`:42-56`) |

The variants differ in ways that matter: `structure_rules._is_elem` accepts a `/Type` of StructElem only, the
others accept anything that isn't MCR or OBJR; `structure_rules` reads a RoleMap without the Name filter the others
apply; the number-tree readers differ in whether they follow `/Kids` and `/Limits`. #413 adds rules over the kept
tree, so it will need these helpers again.

**Change.** One module, `app/tagging/tree.py`: `kids(e)`, `is_elem(o)`, `walk(root)` (iterative, with `seen`, the
`tagger._tree_walk` version), `role_map(pdf)` and `resolve_role(raw, role_map)` (moved from `existing_tags`, which
keeps a re-export for its many importers), `number_tree_get(tree, key)`, `new_elem(pdf, role, page, parent=None)`,
`mcr(page, mcid, stm=None)`, `unique_id(ids, base)`, and `STANDARD_TYPES` moved from `existing_tags`. Each copy
becomes a call. The `models` box helpers replace the `_area`, `_overlap`, `_iou` and `_union` copies;
`content_marker.mat_mul`/`apply` replace editor's pair. The `integrity.py` copies stay (`content_marker.py:118` says
`integrity._mcid` is a deliberate copy, so the checker doesn't share code with what it checks; keep that rule and
say so at the top of `tree.py`). That rule is already bent: `editor.py:115`, `semantic.py` and `tagmap.py` import
`integrity._items`; after this item they import `tree.kids` instead, and `integrity` keeps its own.

**Risk.** Low per call site, but there are about forty of them. Two places change meaning if done carelessly:
`structure_rules._is_elem` (keep its `/Type` check as a flag or leave that one copy with a comment) and the RoleMap
read in `structure_rules.apply` (adopting the Name filter is right, but check it on the kept-tags documents).

**Check.** Tests (`test_existing_tags.py`, `test_editor.py`, `test_structure_rules.py`, `test_kept_tags_order.py`,
`test_self_links.py`); tag maps on both sets.

**Size.** ~300 lines (one new module of ~120, the rest deletions). Before item 3, which moves the code these calls
sit in; before #413.

---

## 3. `tag_document` in phases; the kept route in its own module

**Problem.** `tag_document` (`app/tagging/tagger.py:256-631`) is 376 lines and both tagging routes at once. Its
phases, in order: inspect the existing tree and choose the route (`:277-298`); mark every page's content
(`:311-319`); on the kept route, release body artifacts and page-sized Figures and mark those pages again
(`:320-345`); add up marker results (`:346-362`); then, kept route only, a 200-line block (`:364-569`) that defines
five closures (`elem_bbox`, `is_outline`, `locate`, `figure_alt`, `describe`) and runs hide, off-page, grouping
artifacts, blank runs, `existing.repair`, artifact-MCID resolution, orphans, readings, footnotes, empty spans and
link words; then the builder for both routes (`:571-599`); then glyph mapping, the UA part, metadata, structure
rules, figure handles and the save (`:601-630`).

What makes it fragile:

- `figure_alt` (`:406`) reads `math_readings`, which is assigned 70 lines later (`:477`): the closure works only
  because `repair` is called after that line.
- `_resolve_artifact_mcids` (`:715`) and `_mark_as_artifacts` (`:912`) pop entries from `sm.existing` in place, and
  the orphan loop (`:524-542`) depends on their having run; orphans are appended to `sm.mcids` (`:541`) and so
  reach `builder.build` without anything saying so.
- `kinds_of` (`:374`) and `existing_kinds` (`:563`) are the same dictionary built twice.
- The tagger reaches into `ExistingTree`'s private members (`_mark`, `_remove_refs`, `_descendants` at `:220,
  337, 340, 494, 989, 1004, 1083`) and into `builder._normalize_headings` (`:587`); `existing.page_tables` is set
  from outside (`:504`) for `_place_by_drawn_table` to find.
- `_footnotes_onto_kept_tags` (`:924-1021`) and `_readings_onto_kept_tags` (`:1024-1088`) call `existing._mark`
  after `repair` has already summed `report.counts` and `samples` (`existing_tags.py:356-360`), so what they change
  is not counted in the existing-tags report. (A bug to confirm, not fix, in the refactor; fix it in its own PR
  once the counts are in one place.) The same two functions define `plain()` twice (`:934-938`, `:1044-1048`).
- Heading levels are normalised twice on the kept route: `ExistingTree._fix_headings` (`existing_tags.py:1056-1068`)
  and `StructTreeBuilder._normalize_headings` (`struct_tree.py:243-253`, run in `build` and again at
  `tagger.py:587`).

#413 (nesting rules over kept tags) lands exactly in the `:364-569` block and in `structure_rules`, so this is the
file it will make longer.

**Change.** Split along the phases that already exist:

- `app/tagging/tagger.py` keeps `tag_document`, now about 120 lines: open, `inspect`, `mark_pages`,
  `repair_kept(...)` (kept route) or `strip`, `build_tree`, `finish` (glyphs, metadata, structure rules, handles,
  save). It passes a small `TagContext` (pdf, analysis, marks, resolvers, `existing_bbox`, `kinds_of`, `recursable`,
  the `Conformance` of item 1) instead of a dozen locals.
- `app/tagging/kept_repair.py` (new) takes the `:364-569` block: the five closures become methods of a `KeptRepair`
  class holding the context, in the order they run today, with `math_readings` computed in `__init__` so the
  late-binding dependency disappears. `_footnotes_onto_kept_tags`, `_readings_onto_kept_tags`,
  `_reading_order_findings`, `_form_pages`, `_resolve_artifact_mcids`, `_mark_as_artifacts`, `_hidden_text`,
  `_off_page` and `_figure_handles` move with it. The two `plain()`s become one.
- `ExistingTree` gets the three public methods the tagger needs (`mark`, `remove_refs`, `descendants`) and a
  `page_tables` constructor argument; `_normalize_headings` is run once, by the builder, and `tagger.py:587` calls a
  public `builder.normalize_headings(root)`.
- The content-stream rewrite loop shared by `_resolve_artifact_mcids` and `_mark_as_artifacts` (`:719-723` =
  `:902-907`) becomes one `rewrite_streams(pdf, marks, edit)` in `content_marker.py`.

`struct_tree.py` (750 lines) and `existing_tags.py` (1,250 lines) stay as they are in this item; they are long but
each is one class with one job, and `ContentMarker._process` (`content_marker.py:201-654`, 454 lines, one operator
loop) is a state machine that reads better whole than cut.

**Risk.** Medium: it moves the most-tested code in the engine, and the order of the kept-route steps is the
behaviour (hide before repair, artifact resolution before orphans). Move code without reordering; keep each step's
comment with it. The tag maps catch any slip, since every kept-tags document in the sets exercises this path.

**Check.** Tests (`test_existing_tags.py`, `test_tagging.py`, `test_readings_on_kept_tags.py`, `test_footnotes.py`,
`test_form_pages.py`, `test_content_claimed_twice.py`, `test_kept_tags_order.py`, `test_figure_outlines.py`,
`test_existing_figure_alts_in_place.py`); tag maps on both sets, which must be byte-identical; PAC on the kept-tags
documents of the quick set once, since PAC checks nesting that veraPDF does not.

**Size.** ~700 lines moved, ~60 new. Depends on items 1 and 2 (do them first so this move isn't also a rename).
Before #413.

---

## 4. `structure.py` as a package of passes with an explicit order

**Problem.** `app/pipeline/structure.py` (945 lines) is one module for headings, lists, captions, footnotes,
endnotes, table notes, running artifacts, drawn lines, repeated images and badges, formulas, columns and reading
order. `assign_roles` (`:29-41`) runs twelve passes whose order is the behaviour, and nothing but position says why:

- `_mark_drawn_lines` must run before `_assign_formulas` and `_assign_headings`, or a row of dashes with a dot is a
  Formula (#438; `:150-152`).
- `_mark_running_artifacts` must run before `_assign_headings`, which skips `Artifact` (`:236`).
- `_assign_headings` must run before `_mark_heading_icons` (reads `H*` roles, `:77`) and before
  `_assign_lists_and_captions` (which only touches `P`, `:618`; a heading candidate with a bullet label is decided
  in `_is_heading_candidate`, `:311-318`, not here).
- `_assign_lists_and_captions` before `_attach_list_continuations` (needs `LI`), before `_assign_formulas` (only
  `P`), before `_mark_footnotes` (needs `P`/`LI`, `:354`), before `_mark_endnotes` (`_note_number` reads
  `list_label`, `:405`).
- `_mark_footnotes` before `_mark_endnotes` (endnotes take only marks "no footnote claimed", `:490`) and before
  `_mark_table_notes` (which skips marks already in `t.notes`, `:582`).
- `_mark_endnotes` calls `heuristic_reading_order` itself (`:422, 485`), before `prepare_analysis` has set
  `page.reading_order` (`orchestrator.py:123-129`): a pass that depends on a later stage's function.

`_is_heading_candidate` (`:295-321`) has grown seven exclusions in a row (#597 added the list-label and
larger-print rules at `:311-318`, #604 the `_SECTION_LABEL` exception at `:299-302`, #438 the row-of-marks test at
`:303-308`), each a sentence of comment and a condition; the next exception goes at the end whether or not it
belongs there. The module's regexes are used by `analyzer.py` (`:13` imports `BULLET_ONLY`, `BULLET_START`,
`FOOTNOTE_MARK`), so the analyzer depends on the structure module's constants while the structure module works on
the analyzer's output.

The last four engine fixes each touched this file and `analyzer.py` together (`git log`: #597, #598, #604, #607),
which is the sign that the split is between concerns inside the file, not between the two files.

**Change.** A package, `app/pipeline/structure/`, one module per concern, each exposing the passes it owns:

| Module | From `structure.py` |
|---|---|
| `__init__.py` | `assign_roles`, re-exports of the public names (`heuristic_reading_order`, `detect_columns`, `caption_pairs`, `footnote_words`, `right_to_left`, `row_top`, `needs_ai_reading_order`) so no importer changes |
| `artifacts.py` | `_mark_drawn_lines`, `_mark_running_artifacts`, `_mark_repeated_images`, `_mark_heading_icons`, `_mark_text_badges`, `_icon_shaped` (`:44-213`) |
| `headings.py` | `_assign_headings`, `_is_heading_candidate`, `_fix_heading_skips` (`:215-337`) |
| `lists_captions.py` | `_assign_lists_and_captions`, `_attach_list_continuations`, `caption_pairs` (`:121-144, 615-662`) |
| `formulas.py` | `_assign_formulas`, `_mostly_math` (`:163-182`; `_is_heading_candidate` imports `_mostly_math`) |
| `notes.py` | footnotes, endnotes, table notes and the spoken marks (`:340-612`) |
| `reading_order.py` | `row_top` to `needs_ai_reading_order` (`:665-945`) |

`assign_roles` becomes a list of `(pass, needs)` pairs, checked once at import:

```python
PASSES = [
    (artifacts.mark_drawn_lines, ()),
    (artifacts.mark_running_artifacts, ()),
    (artifacts.mark_repeated_images, ()),
    (headings.assign, ("mark_drawn_lines", "mark_running_artifacts")),  # a row of dashes isn't a heading; nor is a page number
    ...
    (notes.mark_endnotes, ("mark_footnotes",)),  # only the marks no footnote claimed
]
```

where `needs` names the passes that must have run, and a test asserts each named pass comes earlier. The comments
in the list are the ones scattered through the docstrings now. `_mark_endnotes` keeps calling
`heuristic_reading_order` (changing that changes output), but the dependency is written down at the pass.

`_is_heading_candidate` is rewritten as a list of named exclusions, each a one-line predicate with the issue it came
from, evaluated in the same order:

```python
_NOT_A_HEADING = [
    ("too little text", lambda b, body: sum(ch.isalpha() for ch in b.text) < 3),
    ("a sentence", _ends_like_a_sentence),             # unless a section label leads it (#604)
    ("a row of controls or decoration", _mostly_marks),  # #438
    ("a formula in larger print", lambda b, body: _mostly_math(b)),
    ("a list item at body size", _body_size_list_item),
    ("a list item, even in larger print", _list_item_in_larger_print),  # #597
]
```

so the next exception is a named entry, and a test can list them.

**Risk.** Low for the move (functions move whole; names get a module prefix), moderate for the `_is_heading_candidate`
rewrite (the predicates must short-circuit in the same order; `words`, `label` and `visible` are computed once and
shared, so pass them in a small struct rather than recomputing). The role comparison is exactly the check this
needs, and it must be empty on all 32 documents.

**Check.** Tests (`test_structure.py`, `test_footnotes.py`, `test_endnotes.py`, `test_table_notes.py`,
`test_title_and_parts.py`, `test_captions.py`, `test_reading_columns.py`, `test_reading_rtl.py`,
`test_reading_order_headings.py`, `test_shifted_scripts.py`, `test_text_layout.py`, `test_drawn_lines.py`); the role
comparison on both sets (identical); the sets.

**Size.** ~950 lines moved, ~80 new, plus 9 test files' imports (`tests/engine/test_table_notes.py` and others import
private names such as `_is_heading_candidate`, `_mark_table_notes`, `_say_footnote_marks`, `_by_rows`). Conflicts
with any open layout fix; do it between fixes. Independent of #445/#413, so after them unless a layout fix is next.

---

## 5. Named block-split rules in `_extract_blocks`

**Problem.** `app/pipeline/analyzer.py:737-865` (`_extract_blocks`, 129 lines) decides where one block ends and
the next begins in one expression (`:809-826`): a bullet glyph alone on its line (`:811`), a bullet or label start
(`:815`), a raised note number under another (`:818`, #598), a bold centred line over a bold margin line (`:820`,
#604), the same row (`:822`), and finally the style match (`:823`). Each fix adds a `starts_item = starts_item or
(...)` line. The same "do these two lines belong together" question is asked again in `_merge_paragraph_lines`
(`:887-904`: style, alignment, gap, not a bullet, not two notes) and in `_attach_list_continuations`
(`structure.py:121-144`: indent, gap, size), with the gap and size tolerances written out each time
(`-0.3 * size <= gap <= 0.8 * size` at `:898`, `0.9 * size` at `structure.py:136`, `0.6` at `:823` and `:896`).

**Change.** The predicate at `:809-826` becomes `_starts_new_block(prev_group, piece, page_width) -> str | None`,
returning the name of the rule that fired (or `None`), as a list of `(name, test)` pairs in the current order:
`"bullet glyph alone"`, `"list marker"`, `"next note"`, `"centred title over margin line"`, `"same row"`,
`"style change"`. The rule name goes into a debug log line (nothing else changes), so a regression can say which
rule split a block. `_merge_paragraph_lines`'s test becomes `_continues_paragraph(prev, b)` with the same shape.
The tolerances become named constants at the top of the text-block section (`_STYLE_SIZE_TOL = 0.6`,
`_LINE_GAP = (-0.3, 0.8)`), used by both. The structure module's `_attach_list_continuations` stays where it is
(it works on blocks with roles) but uses the same constants (after item 6, which gives the two modules a shared
place to put them).

**Risk.** Low: the expression is reordered into a list without changing its order or operands. The role comparison
catches any slip, since a block split differently changes ids and text hashes for the rest of the page.

**Check.** Tests (`test_text_layout.py`, `test_shifted_scripts.py`, `test_footnotes.py`, `test_endnotes.py`,
`test_title_and_parts.py`, `test_visual_lines_speed.py`); role comparison (identical); the sets.

**Size.** ~150 lines in `analyzer.py`. After item 4 if both are done; otherwise independent.

---

## 6. Shared marker regexes

**Problem.** The patterns for "what a list label, a footnote mark or a note number looks like" are defined in four
places, differently: `structure.py:12-26` (`_BULLET`, `BULLET_ONLY`, `FOOTNOTE_MARK`, `_FOOTNOTE_LEAD`,
`_LIST_LABEL`), `structure.py:394-395` (`_NOTE_NUMBER`), `structure.py:564-566` (`_TABLE_MARK`, `_TABLE_NOTE`,
`_TABLE_MARK_NAMES`, a second copy of `_MARK_NAMES` at `:532`), `analyzer.py:82` (`_LABEL_RUN`, the same label
classes as `_BULLET` but as a whole run), and `app/pipeline/lists.py:25-57` (`_style`, `_core`, `roman_value`,
`label_kind`, a third reading of the same labels). `analyzer.py:13` imports three of structure's; `struct_tree.py`
imports `lists`. A change to what counts as a label (a new bullet glyph, a "(iv)" variant) is made in one place and
missed in another.

**Change.** `app/pipeline/markers.py`: the bullet character set, the label and bullet regexes, the footnote and
table-mark regexes, one `MARK_NAMES` dict and `footnote_words`, and `lists.label_kind`'s helpers moved in (or
`lists.py` imports from it). `analyzer.py`, `structure.py` (or its package), `lists.py` and `struct_tree.py` import
from `markers`. No pattern changes.

Two things found here are not for this item, because unifying them would change output; file each as an issue:

- `struct_tree._list_numbering` (`struct_tree.py:65-79`) classifies list labels again with its own regexes
  (`[ivxlc]+` is always roman; its bullet set is `("•", "●", "·")`, not `BULLET_CHARS`), and disagrees with
  `lists.label_kind` / `roman_value` (`lists.py:40-92`), which use the sequence to tell "(i) after (h)" from a
  roman numeral. A lone `c`, `l` or `v` label gets a roman `/ListNumbering` from one and a letter kind from the
  other. `struct_tree` already imports `lists`.
- The caption pattern exists in four versions with different word lists: `structure.py:18` (`figure|fig\.|table|
  chart|exhibit|image` + digit), `plots.py:23` (adds `graph|plate|example`), `schemes.py:37` (adds `scheme|eq`),
  `semantic.py:741-742` (inline, twice). Each was tuned for its caller; a shared pattern is a behaviour change.
  Put them side by side in `markers.py` under one comment, unchanged.

Smaller pairs to fold in as they are met: `_LANGUAGE_TAG` (`orchestrator.py:278`) and `editor._LANG` (`:146`); the
font-subset prefix `^[A-Z]{6}\+` at `font_embed.py:32`, `semantic.py:29` and inside `content_marker.py:738`;
`classify._GREEK` (`:29`) and `notation._GREEK_CHARS` (`:44`); the equation-number label at `mathml.py:84, 221`,
`notation.py:679` and `mathspeak.py:89`.

**Risk.** Very low if the patterns are moved byte for byte. The one temptation to resist is unifying `_LABEL_RUN`
with `_BULLET` (they anchor differently); keep both, next to each other, with a line saying why.

**Check.** The role comparison (identical) and the structure tests above.

**Size.** ~120 lines. Natural to do with item 4 (it removes `analyzer.py`'s import from the structure module, which
would otherwise point at a package `__init__`).

---

## 7. The run's result built in one place

**Problem.** The run's `metrics` dictionary is built at `orchestrator.py:553-576`, then patched at `:589-592`
(report-only), `:631-635` (after describing existing figures: the AI usage is written a second time), `:808-812`
(verification tokens), `:843-846` and again at `:880-881` (processing time, twice, the second after the summary),
and the `result` dictionary is assembled twice with the same shape (`:593-607` report-only, `:849-878` the full
run). `adobe_job.py:205-266` builds a third `metrics` and `result` of the same shape by hand, importing nine private
helpers from the orchestrator (`_read_formulas, _snapshot, _tagmap_file, _validate, _verdict_line,
_visual_fidelity, _write_json_report`, `adobe_job.py:33`), and `edit_job.py:15, 100-102` a fourth, importing six
more. The report readers (`reports/markdown.py`, `reports/pdf.py`, the portal) rely on the keys agreeing across the
four, and nothing checks that they do.

**Change.** `app/pipeline/run_result.py` with two dataclasses, `RunMetrics` (every key of `metrics` as a typed field
with its default: `pages_processed`, `ai_tokens_used`, …, `processing_time_seconds`, `stage_seconds`, `limits_hit`)
and `RunResult` (`files`, `metrics`, `reports`, `warnings`, with `to_dict()`), plus `ai_usage(ai)` for the five
fields written at `:555-559` and `:631-635`. `run_remediation` fills one `RunMetrics` as it goes and calls
`to_dict()` once at the end; the report-only return and the Adobe and edit jobs build the same `RunResult`. The
private helpers the two jobs import become public functions of a small `app/pipeline/outputs.py` (`validate`,
`snapshot`, `tagmap_file`, `visual_fidelity`, `write_json_report`, `verdict_line`, `move_xml`), which the
orchestrator imports too.

**Risk.** Low to medium: the JSON must come out key-for-key the same, and the portal reads it. A test that builds a
`RunResult` and compares its keys with a recorded report JSON from the regression output guards that.

**Check.** Tests (`test_metrics.py`, `test_stage_timings.py`, `test_adobe.py`, `test_editor.py`'s job tests,
`tests/portal` for the report readers); the sets, diffing each `*_remediation_report.json` before and after with
the time fields masked.

**Size.** ~400 lines across `orchestrator.py`, `adobe_job.py`, `edit_job.py`. Independent of #445/#413 and of items
1-6.

---

## 8. `run_remediation` as named stages

**Problem.** `run_remediation` (`orchestrator.py:395-886`) is 492 lines: six numbered stages, each 40-170 lines,
sharing some thirty locals (`base`, `analysis`, `enrichment`, `ai`, `scanned`, `ocr_count`, `scan_figure_count`,
`music_*`, `fields_named`, `title`, `language`, `visual`, `tag_visual`, `vera_visual`, `widths_subpixel`,
`fidelity`, `modified`, `tag_modified`, `vera_modified`, `destructive_pages`, `fonts_embedded`, `pv`, `rv`, …). The
destructive stage (`:681-760`) chooses between three candidate files with `fonts_only`, `widths_subpixel` and
`candidate` as the state, and the tagging call is written out four times (`:619-622, 639-642, 709-712, 739-742`)
with the same nine keyword arguments. The OCR branch (`:436-488`) is 50 lines inside an `if` inside the analysis
stage.

**Change.** After item 7, one class per run, `Remediation`, holding the shared state as attributes, with one method
per stage in the order the docstring (`:1-14`) already gives: `before()`, `analyze()` (with `_ocr()` and `_music()`
split out), `enrich()`, `tag_visual()`, `fix_or_flatten()`, `report()`. `run_remediation` becomes the dozen lines
that call them. The four `tag_document` calls become one `self._tag(src, dst, analysis, layout_tables,
region_alts=None)`. The report-only early return becomes `report_only()`. No stage's body changes.

**Risk.** Medium only because of size; the stage bodies move without edits. `StageClock` and the `_clock`
contextvar (`:132-167`) stay as they are. The `progress` percentages stay inline.

**Check.** The whole engine test suite (many tests drive `run_remediation` with the mock provider); the sets, with
report JSONs diffed as in item 7.

**Size.** ~500 lines moved, ~80 new. After item 7. Independent of #445/#413.

---

## 9. `editor.py` as a package, with a dispatch table

**Problem.** `app/tagging/editor.py` (2,503 lines) is the tag manager's twenty-odd edit operations in one file,
with three parallel chains that must agree: `OPS` (`:117-120`), the per-op validation chain in `validate_ops`
(`:177-293`, 128 lines), the target-resolution chain in `apply_edits` (`:351-368`) and the dispatch chain in
`_apply` (`:391-463`, falling through to `EditError`). A new op is added in four places. The file groups cleanly
by concern already (role change `:466-591`; text properties `:592-631`; table cells and Headers/ID `:632-836`;
tree primitives `:837-888`; move/decorative/delete `:889-1084`; merge `:1085-1173`; lists `:1174-1448`; TOC
`:1449-1561`; split `:1562-1783`, including a content-stream rewrite; areas and ParentTree arrays `:1784-1925`;
cut area and tag artifacts `:1926-2263`; the table editor `:2264-2503`), and `_SHOW` is defined twice (`:1598`,
`:1930`). The longest functions are `_cut_area` (`:2041-2180`, 140 lines), `validate_ops` and `_build_list`
(`:1243-1355`).

**Change.** A package `app/tagging/editor/`: `__init__.py` (the public `validate_ops`, `apply_edits`, `EditError`,
`OPS`), `_ctx.py` (`_Ctx`, the tree primitives `:837-888`, `_give_id`, `_reparent_items`, `_number_tree_get` and
friends — after item 2, several of these come from `tree.py`), then one module per group above: `roles.py`,
`text.py`, `tables.py` (cells, headers, and the table editor `:2264-2503`), `structure.py` (move, decorative,
delete, merge, split), `lists.py` (lists and TOC), `areas.py` (areas, cut area, tag artifacts, the content-stream
split `:1596-1783`). Each op module registers itself in one table:

```python
OPERATIONS: dict[str, Operation] = {}   # op name -> (validate, resolve_target, apply)
```

so `validate_ops`, `apply_edits` and `_apply` each become a lookup, and `OPS` is `OPERATIONS.keys()`. The op order
inside `_apply` has no meaning today (each edit is applied in the order sent, `:373`), so a table changes nothing.

**Risk.** Low per op; the volume is the risk. `tests/engine/test_editor.py` (64 tests, every op but three) plus
`test_table_headers.py`, `test_footnotes.py:205` and `test_field_labels.py:130` cover the rest. The tag manager's
saves run through `edit_job.py`, so the quick set does not exercise this code; the tests are the check.

**Check.** Tests; `tests/portal` (the API's validation errors are asserted there); one manual save of each op kind
in the tag manager against a local stack.

**Size.** ~2,500 lines moved, ~100 new. Touches nothing #445/#413 touch. Any time; best after item 2.

---

## 10. Test fixtures for pages and blocks

**Problem.** `tests/engine/conftest.py` (32 lines) has two fixtures (`samples`, `verapdf_available`). Every test that
needs an analysis builds its own: fourteen files define a `_page(...)` or `_block(...)` that constructs
`PageAnalysis`/`TextBlock` by hand (`test_captions.py:14`, `test_reading_columns.py:14`, `test_reading_rtl.py:16`,
`test_placement.py:117`, `test_reading_order_headings.py:6`, `test_flattened_headings.py:6, 12`,
`test_shifted_scripts.py:51`, `test_plots.py:26`, `test_cap_notes.py:91`, `test_endnotes.py:64`,
`test_title_and_parts.py:55`, `test_notation.py:282`, `test_music.py:413`, …), and nine more draw a small PDF with
PyMuPDF to analyse (`test_footnotes.py:19`, `test_text_layout.py:21`, `test_language.py:47`, `test_form_pages.py:15`,
`test_scanned_forms.py:21`, `test_notation.py:18`, `test_fraction_bar.py:8`, …). Each new structure test copies one.
The tagging tests do the same with PDFs: a one-page pikepdf file (Helvetica, `Pages/Kids/Count=1`) is built by hand
in 21 files (`test_mcid_bound.py:14`, `test_readings_on_kept_tags.py:11`, `test_table_span_bound.py:13`,
`test_odd_mcid_values.py:19`, `test_picture_glyphs.py:24`, …); `_errors(path)` (run the integrity check, keep the
errors) is byte-identical in `test_bookmarks.py:38`, `test_editor.py:35` and `test_verification.py:28`, and the
same filter is written inline about 36 more times in 26 files; a full mock-provider `run_remediation` is set up the
same way in 12 files (`test_captions.py:85`, `test_hostile_text.py:37`, `test_language.py:66`,
`test_protection.py:122`, …); `_pixels` is identical in `test_editor.py:41` and `test_footnotes.py:149`; `_swallow`
in `test_adobe.py:221` and `test_api.py:31`; the same-render check (`get_pixmap(dpi=72).samples ==`) in six files.

**Change.** `tests/engine/layout.py` (a helper module, not a conftest, so imports are explicit): `block(id, bbox,
text, role="P", size=10.0, bold=False, **fields) -> TextBlock`, `page(*elements, width=612, height=792, order=None)
-> PageAnalysis`, `document(*pages, body=10.0) -> DocumentAnalysis`, and `draw_pdf(path, lines)` for the "write a
few lines at positions and analyse them" tests. A second, `tests/engine/pdfs.py`: `one_page(path, content, fonts=
...)`, `integrity_errors(path)`, `remediate(tmp_path, src, **opts)`, `same_render(a, b)`. Existing tests switch to
them one file at a time as they are touched; no test's assertions change.

**Risk.** None to the engine. **Check.** The tests pass. **Size.** ~200 lines. Any time.

---

## 11. Dead code, duplicate helpers, doubled tests

Noted, not fixed. Each is a one-line change; together they make one small PR.

**Dead** (each confirmed by grep; no module in `app/` is unimported):
- `ExistingTree._replace_kid` (`app/tagging/existing_tags.py:614-617`): no caller in `app/` or `tests/`.
- `targets.ARTIFACT_PAGINATION` (`app/tagging/targets.py:16`): never referenced (`:47` builds the string inline).
- `notation._CHEM_FORMULA` (`app/pipeline/notation.py:25`): unused; `_SOLID_FORMULA` (`:621`) is its near-twin.
- `font_embed.substitutes_available()` (`app/tagging/font_embed.py:40`) and `integrity.CHECKS`
  (`app/verification/integrity.py:32`): unused.
- Referenced only by tests: `music.find_staves` (`music.py:66`, a one-line wrapper; `test_music.py:37`) and
  `semantic.check_semantics` (`semantic.py:989`; `test_verification.py:126`, `test_finding_boxes.py:55`; the app
  uses `SemanticChecker` directly). Either the tests use what the app uses, or the wrappers are kept on purpose.
- Written and never read: `ExistingTree.top` (`existing_tags.py:143, 169`), `links_wrapped` (`:145, 565`),
  `double_claims` and `double_claim_pages` (`:147-148, 269`).
- `editor._SHOW` defined twice (`editor.py:1598`, `:1930`), the second replacing the first.

**Duplicate helpers** (beyond the tree helpers of item 2 and the regexes of item 6):
- The reading-service queue client is written twice: `formula_ocr._client` / `read_all` (`app/pipeline/formula_ocr.
  py:50-99`) and `omr._client` / `read_all` (`omr.py:27-79`) differ only in the timeout setting, the log prefix and
  the answer key (`latex` cut to 4,000 characters vs base64 `musicxml`); `omr.read_score` (`:81`) is the same
  push/blpop/lrem a third time. One `read_all(queue, result_key, timeout, decode)` covers all three, and the doubled
  tests below go with it. About 100 lines; its own small PR.
- `_MARK_NAMES` (`structure.py:532`) and `_TABLE_MARK_NAMES` (`:566`) are the same dict.
- `plots._is_label` (`:26`) and `schemes._is_label` (`:83`) have the same logic (caption check, ≤4 words, ≤40
  characters) over different argument types; `_fat` at `plots.py:91` and `schemes.py:242` pad a box the same way.
- "Most common font size, else 10.0" is written at `analyzer.py:1383-1387` (weighted by text length),
  `semantic._body_size` (`:622-634`, rounded to 0.5 pt) and `semantic.py:154-156`; `sorted(x)[len(x) // 2]` medians
  appear about nine times in `analyzer.py` alone.
- Whitespace collapsing is `analyzer._clean` (`:38`), `re.sub(r"\s+", " ", …)` at `field_labels.py:72`,
  `structure.py:600`, `notation.py:410`, and `" ".join(x.split())` in seven more places.
- `language.primary()` (`language.py:54`) is redone inline at `semantic.py:910, 926`.
- `plain()` in `tagger._footnotes_onto_kept_tags` (`:934-938`) and `_readings_onto_kept_tags` (`:1044-1048`).
- `kinds_of` (`tagger.py:374`) and `existing_kinds` (`:563`): the same dictionary built twice.
- `_set_table_attr` (`existing_tags.py:943-957`), `_layout` (`structure_rules.py:53-67`) and `_set_scope`
  (`adobe_job.py:400-414`) all find-or-append the attribute dictionary with a given `/O`.
- The "unmatched" key is built from the same pattern at `targets.py:43`, `struct_tree.py:423` and `tagger.py:540`.
- `_clear_stale_struct_keys` (`tagger.py:93-114`) and `ExistingTree.strip` (`existing_tags.py:1240-1250`) both clear
  `/StructParents`, and run one after the other at `tagger.py:291-296`; `strip` misses form XObjects.
- `adobe_job._header_scopes` / `_set_scope` (`:339-414`) re-implement TH scope inference that `existing_tags.
  _infer_scope` (`:1134-1140`) and `struct_tree._table` (`:544-546`) each do their own way: three rules for one
  attribute. Not a refactor to do blind — the three rules give different answers — but worth one issue.

**Doubled tests:**
- `test_formula_reader.py:41, 48` and `test_music.py:172, 181`: the same test names and assertions, over the two
  copies of the queue client, with the `queue` fixture (`:21` / `:151`) and `_serve` (`:26` / `:157`) copied too.
- `test_editor.py:154` (`test_table_cell_header_and_scope`) and `test_table_headers.py:125` both flip a cell TD↔TH
  with `set_cell`; `test_table_headers.py:194` does it a third time with header links.
- `test_editor.py:202` and `:214` both assert that a split adds one element (the second also checks the role).

**Possible bug found on the way** (file as an issue; not part of any refactor): `existing_tags.repair` sums
`report.counts` and `samples` at `:356-360`, but `_readings_onto_kept_tags` and `_footnotes_onto_kept_tags`
(`tagger.py:547, 555`) call `existing._mark` after that, so the paragraphs they turn into Notes and the tags they
give readings are missing from the existing-tags counts in the report.

---

## What this review did not find worth a refactor

- `app/tagging/content_marker.py` `_process` (`:201-654`): one 454-line operator loop with its helpers nested above
  it. It is a state machine over a content stream, and the state (graphics, text, marked-content nesting, forms) is
  the point; cutting it into methods would add a class whose attributes are those locals, for no gain in what can
  be changed safely. Leave it.
- `app/tagging/struct_tree.py` (750 lines) and `app/tagging/existing_tags.py` (1,250 lines): one class each, with
  one job each, and their long functions (`_repair_element` at 90 lines, `_place_by_drawn_table` at 86) are each
  one rule. Item 2 shortens them a little; nothing more is needed now.
- `app/verification/semantic.py` (992 lines) and `tagmap.py`: not looked at closely; they are checkers, not in the
  path of #445/#413, and were not touched by the recent fixes.
- `app/pipeline/music.py`, `notation.py`, `schemes.py`, `plots.py`, `mathml.py`: each is one feature behind one
  entry point, called from the orchestrator or the analyzer; their size is their domain's.

## What to do first

Item 0 (the comparison tool), then item 1 (the conformance target), then item 2 (tree helpers). Item 1 is the
groundwork of #445 and can be merged as its first part; item 2 then makes item 3 a move rather than a rewrite, and
item 3 leaves #413 a module of its own to land in. Items 4-6 wait for the next lull in layout fixes, since they
conflict with any open one; items 7-11 fit anywhere.
