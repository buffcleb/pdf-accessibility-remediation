"""Footnotes, endnotes and table notes, and how their marks are said."""
from __future__ import annotations

import re

from app.pipeline.models import DocumentAnalysis, PageAnalysis, TextBlock, append_block
from app.pipeline.markers import (FOOTNOTE_LEAD, FOOTNOTE_MARK, MARK_NAMES, NOTE_NUMBER, PAGE_NUM, TABLE_MARK, TABLE_NOTE,
                                  footnote_words)
from app.pipeline.structure.reading_order import heuristic_reading_order


def mark_footnotes(doc: DocumentAnalysis) -> None:
    """Footnotes: small print in the lower part of a page that starts with a mark (a number or *, †, ...) raised
    after a word in the larger text above it on the same page. The mark is the evidence: small numbered lines at
    the foot of a page are just as often a table's values, list items or captions, and those have no raised mark
    pointing at them. A footnote becomes a Note; the blocks whose marks point at it remember it (see notes)."""
    body = doc.body_font_size
    if not body:
        return
    for p in doc.pages:
        pointing = [b for b in p.blocks if b.marks and b.role != "Artifact"]
        if not pointing:
            continue
        marks = {mk for r in pointing for mk in r.marks}
        for b in list(p.blocks):
            if b.role not in ("P", "LI") or b.font_size > 0.9 * body or b.bbox[1] < 0.55 * p.height:
                continue
            m = FOOTNOTE_LEAD.match(b.text)
            lead = b.lead_mark or (m.group(1) if m else None)
            alone = None if lead else _lone_mark(p, b, marks)
            if alone is not None:
                lead = alone.text.strip()
            words = b.text.split()
            letters = sum(ch.isalpha() for ch in b.text) / max(1, len(b.text.replace(" ", "")))
            if not lead or len(words) < 4 or letters < 0.5:
                continue  # a value, a lone number or a row of figures, not a note
            src = [r for r in pointing if r is not b and lead in r.marks and r.font_size > b.font_size
                   and r.bbox[3] <= b.bbox[1] + 1]
            if not src:
                continue
            if alone is not None:  # the note's number, set apart by a gap, is part of the note
                b.text = f"{lead} {b.text}"
                if b.actual_text:
                    b.actual_text = f"{lead} {b.actual_text}"
                b.bbox = (min(b.bbox[0], alone.bbox[0]), min(b.bbox[1], alone.bbox[1]), max(b.bbox[2], alone.bbox[2]),
                          max(b.bbox[3], alone.bbox[3]))
                p.blocks.remove(alone)
                if alone.id in p.reading_order:
                    p.reading_order.remove(alone.id)
            b.role, b.list_label, b.note_mark = "Note", None, lead
            for r in src:
                if b.id not in r.notes:
                    r.notes.append(b.id)
        notes = {b.id: b.note_mark for b in p.blocks if b.role == "Note" and b.note_mark}
        for r in pointing:
            by_mark = {notes[n]: n for n in r.notes if n in notes}
            r.mark_notes = [by_mark.get(mk) for mk in r.marks]
            say_footnote_marks(r, set(by_mark))


# An endnotes section's heading ("Notes", "Endnotes"; not "Notes:", which leads into what follows it), and the headings
# that close it (back matter)
_NOTES_HEAD = re.compile(r"\s*(end\s*)?notes\s*", re.I)
_BACK_MATTER = re.compile(r"\s*(bibliography|references|works cited|sources|index|appendix|glossary)\b", re.I)
# A notes page's running header ("Notes to Pages 17–30") and a bare page number
_NOTES_RUNNING = re.compile(r"^\s*(\d{1,4}\s+)?notes\s+(to|for)\s+(pages?|chapter)\b.*$", re.I)
# Without a mark in the text pointing at it, a section is taken for endnotes only when it holds this many: a few
# numbered lines under "Notes" are as often a list of tips
_UNPOINTED_NOTES = 10


def _note_number(b: TextBlock) -> int | None:
    """The number an entry of an endnotes section starts with: raised ("¹Smith"), or written ("1. Smith", "1 Smith")."""
    if b.lead_mark and b.lead_mark.isdigit():
        return int(b.lead_mark)
    m = re.fullmatch(r"(\d{1,3})[.)]", b.list_label or "") or NOTE_NUMBER.match(b.text)
    return int(m.group(1)) if m else None


def mark_endnotes(doc: DocumentAnalysis) -> None:
    """Endnotes: a section headed "Notes" or "Endnotes" whose first entry is numbered 1, running until a heading of
    its own rank or higher, a back-matter heading (Bibliography, References, Index...) or the end. Each numbered
    entry becomes a Note, in sequence: the number goes up by one, or back to 1 where a new chapter's notes start
    under a short unnumbered label ("Chapter 1"), which becomes a heading below the section's. An unnumbered block
    after a note continues it (joined to it on the same page, its tag's content on the next: continues). The pages'
    running headers ("Notes to Pages 17–30") and page numbers are artifacts.

    The raised marks outside the section that no footnote claimed point at these notes, by number: the marks read in
    order, each fall back to 1 starting the next chapter's notes. Only when the marks fall into as many such runs as
    the section has groups is each run matched to its group; otherwise the marks are left as they are. A section no
    mark points at is taken only when it is long (_UNPOINTED_NOTES)."""
    body = doc.body_font_size
    flow = [(p, b) for p in doc.pages for eid in heuristic_reading_order(p)
            if isinstance(b := p.element(eid), TextBlock)]
    for n, (p, b) in enumerate(flow):
        if not (b.role.startswith("H") or b.role == "P") or not _NOTES_HEAD.fullmatch(b.text):
            continue
        nxt = [x for _, x in flow[n + 1:n + 4] if not _NOTES_RUNNING.match(x.text) and not PAGE_NUM.match(x.text)]
        if not any(_note_number(x) == 1 for x in nxt[:2]):
            continue
        groups, changes = _endnote_section(flow[n + 1:], b, body)
        links = _endnote_links(doc, groups, {id(c[1]) for c in changes})
        if links or sum(map(len, groups)) >= _UNPOINTED_NOTES:
            _apply_endnotes(doc, changes, links)
            return


def _endnote_section(rest: list, head: TextBlock, body: float) -> tuple[list[dict[int, TextBlock]], list[tuple]]:
    """The notes of an endnotes section starting after head, by group (a chapter's notes), each by number, and what
    to change to tag them: (kind, block, the note it continues, its page), nothing changed yet."""
    level = int(head.role[1]) if head.role.startswith("H") else None
    groups: list[dict[int, TextBlock]] = []
    changes: list[tuple] = []
    last: TextBlock | None = None
    last_page: PageAnalysis | None = None
    for i, (p, b) in enumerate(rest):
        if b.role.startswith("H") and (_BACK_MATTER.match(b.text) or (level and int(b.role[1]) <= level)):
            break
        near_edge = b.bbox[3] <= p.height * 0.12 or b.bbox[1] >= p.height * 0.88
        if near_edge and (_NOTES_RUNNING.match(b.text) or PAGE_NUM.match(b.text)):
            changes.append(("furniture", b, None, p))
            continue
        if b.role not in ("P", "LI", "Note") and not b.role.startswith("H"):
            continue
        num = _note_number(b)
        if num == 1 or (num is not None and groups and num in (max(groups[-1]) + 1, max(groups[-1]) + 2)):
            if num == 1:
                groups.append({})
            groups[-1][num] = b
            changes.append(("note", b, num, p))
            last, last_page = b, p
            continue
        following = next((x for _, x in rest[i + 1:i + 3] if not _NOTES_RUNNING.match(x.text)), None)
        if (len(b.text.split()) <= 8 and not b.text.rstrip().endswith((".", ",", ";"))
                and following is not None and _note_number(following) == 1):
            if level:  # a chapter's notes start under it
                changes.append(("label", b, level + 1, p))
            last = None
            continue
        if b.role.startswith("H"):
            break  # a heading of a lower rank that labels nothing: the section has ended
        if last is None or b.font_size > max(last.font_size, body) * 1.15:
            if groups:
                break
            continue
        changes.append(("joined" if last_page is p else "carried", b, last, p))
    return [g for g in groups if g], changes


def _endnote_links(doc: DocumentAnalysis, groups: list[dict[int, TextBlock]], section: set[int]) -> list[tuple]:
    """The raised marks outside the section that no footnote claimed, matched to the endnotes by number: (block, the
    mark's index, the note)."""
    runs: list[list[tuple[TextBlock, int]]] = []
    prev = None
    for p in doc.pages:
        for eid in heuristic_reading_order(p):
            b = p.element(eid)
            if not isinstance(b, TextBlock) or id(b) in section or b.role in ("Note", "Artifact") or not b.marks:
                continue
            for i, mk in enumerate(b.marks):
                if (i < len(b.mark_notes) and b.mark_notes[i] is not None) or not mk.isdigit():
                    continue
                if prev is None or int(mk) < prev:
                    runs.append([])
                runs[-1].append((b, i))
                prev = int(mk)
    runs = [r for r in runs if r[0][0].marks[r[0][1]] == "1"]
    if len(runs) != len(groups):
        return []
    return [(b, i, group[int(b.marks[i])]) for run, group in zip(runs, groups) for b, i in run
            if int(b.marks[i]) in group]


def _apply_endnotes(doc: DocumentAnalysis, changes: list[tuple], links: list[tuple]) -> None:
    joined: set[str] = set()
    for kind, b, arg, p in changes:
        if kind == "furniture":
            b.role, b.artifact_type = "Artifact", "Pagination"
        elif kind == "label":
            b.role = f"H{min(arg, 6)}"
        elif kind == "note":
            b.role, b.list_label, b.note_mark, b.note_kind = "Note", None, str(arg), "endnote"
        elif kind == "joined":
            append_block(arg, b)
            joined.add(b.id)
        else:
            b.role, b.list_label, b.continues, b.note_kind = "Note", None, arg.id, "endnote"
    for p in doc.pages:
        p.blocks = [b for b in p.blocks if b.id not in joined]
    said: dict[int, tuple[TextBlock, set[str]]] = {}
    for b, i, note in links:
        if len(b.mark_notes) != len(b.marks):
            b.mark_notes = [None] * len(b.marks)
        b.mark_notes[i] = note.id
        if note.id not in b.notes:
            b.notes.append(note.id)
        said.setdefault(id(b), (b, set()))[1].add(b.marks[i])
    for b, marks in said.values():
        say_footnote_marks(b, marks, "endnote")


def say_footnote_marks(b: TextBlock, noted: set[str], kind: str = "footnote") -> None:
    """A mark written onto a word ("soils¹") is read as part of it ("soils1"). Where the mark has a footnote on the
    page, the read-aloud text says it as one: "soils (footnote 1)". Only the exact word and mark the analyzer saw
    raised change, in order, so an "R1" elsewhere in the paragraph stays as it is."""
    spoken = b.actual_text or b.text
    pos, changed = 0, False
    for mark, before in zip(b.marks, b.mark_after):
        if mark not in noted or not before.strip():
            continue
        glued = before + mark
        at = spoken.find(glued, pos)
        if at < 0:
            at = spoken.find(glued)  # the read-aloud order can differ from the drawing order
        if at < 0:
            continue
        said = f"{before} ({footnote_words(mark, kind)})"
        spoken = spoken[:at] + said + spoken[at + len(glued):]
        pos, changed = at + len(said), True
    if changed:
        b.says_marks_only = b.actual_text is None or b.says_marks_only  # nothing else needed saying: the tags can say the marks instead
        b.actual_text = spoken


def mark_table_notes(doc: DocumentAnalysis) -> None:
    """Notes under a table: a block right below it (within about 120 points, across its width) that starts with a
    symbol (*, †, ...) its cells carry. Measured on a 332-page clinical guideline: such notes sit right under their
    table, and fewer than half are in smaller print, so the symbol in the cells is the evidence, not the size. Letters
    (a, b) aren't taken: a cell's "a" is as likely a word. The note becomes a Note; each cell with the symbol says
    it as a footnote ("120 (footnote asterisk)") and, once tagged, points at the note (struct_tree)."""
    for p in doc.pages:
        for t in p.tables:
            carried = {m for c in t.cells for m in TABLE_MARK.findall(c.text or "")}
            if not carried:
                continue
            for b in p.blocks:
                m = TABLE_NOTE.match(b.text or "")
                if (not m or m.group(1) not in carried or m.group(1) in t.notes or b.role not in ("P", "LI")
                        or len(b.text.split()) < 2 or not (t.bbox[3] - 2 <= b.bbox[1] <= t.bbox[3] + 120)
                        or b.bbox[2] < t.bbox[0] or b.bbox[0] > t.bbox[2]):
                    continue
                b.role, b.list_label, b.note_mark = "Note", None, m.group(1)
                t.notes[m.group(1)] = b.id
            for c in t.cells:
                said = say_table_marks(c.actual_text or c.text or "", t.notes)
                if said != (c.actual_text or c.text or ""):
                    c.actual_text = said


def say_table_marks(text: str, notes: dict) -> str:
    """A cell's symbols that have a note, said as footnotes: "120*" as "120 (footnote asterisk)"."""
    def say(m: re.Match) -> str:
        if m.group(0) not in notes:
            return m.group(0)
        return " (footnote " + " ".join(MARK_NAMES[ch] for ch in m.group(0)) + ")"
    out = re.sub(r"\s+", " ", TABLE_MARK.sub(say, text)).strip()
    return out[1:-1] if out.startswith("(footnote") and out.endswith(")") and out.count("(") == 1 else out  # mark alone


def _lone_mark(page: PageAnalysis, b: TextBlock, marks: set[str]) -> TextBlock | None:
    """A footnote's number typeset with a gap after it comes out as a block of its own: a small block that is only a
    mark found in the text above, ending just left of b's first line."""
    for m in page.blocks:
        if (m is not b and m.role == "P" and m.text.strip() in marks and FOOTNOTE_MARK.match(m.text.strip())
                and b.baseline is not None and abs(m.bbox[3] - b.baseline) < 1.2 * b.font_size
                and m.bbox[2] <= b.bbox[2] - 10 and b.bbox[0] - m.bbox[2] < 25 and m.font_size <= b.font_size * 1.1):
            return m
    return None
