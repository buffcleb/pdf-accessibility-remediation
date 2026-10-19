"""Music notation: find it, make each example one figure, and read it.

A page of printed music is, to the rest of the pipeline, staff lines (rules or sliver images, which look like
decoration) and note heads from a music font (characters with no meaning, which become paragraphs of nothing). Here
each example is found by its staves and becomes one figure, so everything in it is tagged together, and its alt
text is a reading of the notes.

- Staves: five rules of the same length, evenly and closely spaced, found on a low-resolution rendering of the page
  (so it doesn't matter how the producer drew them). Six or more evenly spaced rules are lined paper or a table.
- Examples: staves joined by a brace sit close together and are one example; staves further apart are separate
  ones (so each gets its own reading, and the text between them stays text).
- Reading: the optical music recognition service (Audiveris, see app/pipeline/omr.py) returns MusicXML, which
  `describe` turns into words: clefs, key, time, then the notes measure by measure. Without the service, or when it
  can't read an example, the figure is described by the AI like any other picture.

Scanned pages aren't handled yet: the page is one picture there, and an example would have to be drawn again on
its own first (as app/pipeline/scan_figures.py does for charts).
"""
from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile

import pymupdf as fitz

from app.pipeline.models import BBox, ImageRegion, PageAnalysis, bbox_area, bbox_intersection

log = logging.getLogger(__name__)

_DPI = 100
_MIN_STAFF_PT = 60.0        # a staff shorter than this is a symbol in running text, not an example
_GAP_PT = (2.5, 10.0)       # distance between two lines of a staff (a normal staff: about 5 pt)
_FULL_READING_MEASURES = 8  # longer examples get a summary and their opening, not every note
_FULL_READING_CHARS = 1200


# --------------------------------------------------------------------------- finding staves
def _rules(page: fitz.Page) -> tuple[list[tuple[float, float, float]], float, object]:
    """Long horizontal rules on the rendered page: (y, x0, x1) in pixels, top to bottom; pixels per point; and
    the page as an array of dark pixels."""
    import numpy as np

    pm = page.get_pixmap(dpi=_DPI, colorspace=fitz.csGRAY, alpha=False)
    scale = _DPI / 72.0
    dark = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.stride)[:, :pm.width] < 200  # thin rules come out gray, not black
    need = _MIN_STAFF_PT * scale
    rows = []
    for y in np.nonzero(dark.sum(axis=1) >= need)[0]:
        edges = np.diff(np.concatenate(([0], dark[y].astype(np.int8), [0])))
        starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
        k = int(np.argmax(ends - starts))
        if ends[k] - starts[k] >= need:
            rows.append((int(y), int(starts[k]), int(ends[k])))
    rules: list[list] = []  # [y first, y last, x0, x1]
    for y, x0, x1 in rows:
        last = rules[-1] if rules else None
        if last and y - last[1] <= 1 and abs(x0 - last[2]) <= 3 and abs(x1 - last[3]) <= 3:
            last[1] = y
        else:
            rules.append([y, y, x0, x1])
    return [((r[0] + r[1]) / 2, r[2], r[3]) for r in rules], scale, dark


def find_staves(page: fitz.Page) -> list[BBox]:
    """The staves on a page, as boxes in page space (top line to bottom line), top to bottom."""
    return find_systems(page)[0]


def find_systems(page: fitz.Page, raster: list | None = None) -> tuple[list[BBox], set[int]]:
    """The staves on a page, top to bottom, and which of them are joined to the next one down: a bar line (or the
    line at the start of a system) runs unbroken from one to the other, as in a piano's two staves. Staves that
    merely sit close together, one line of music under another, are not joined. `raster` receives the rendering
    this was read from, (dark pixels, pixels per point), for looking at the same page again."""
    rules, scale, dark = _rules(page)
    if raster is not None:
        raster[:] = [dark, scale]
    lo, hi = _GAP_PT[0] * scale, _GAP_PT[1] * scale
    found, i = [], 0  # (x0, top, x1, bottom) in pixels

    def same_staff(a, b, gap: float | None) -> bool:
        d = b[0] - a[0]
        return (lo <= d <= hi and abs(a[1] - b[1]) <= 3 and abs(a[2] - b[2]) <= 3
                and (gap is None or abs(d - gap) <= max(1.2, 0.12 * gap)))

    while i < len(rules):
        run = [rules[i]]
        gap = None
        while i + len(run) < len(rules) and same_staff(run[-1], rules[i + len(run)], gap):
            run.append(rules[i + len(run)])
            gap = gap or run[1][0] - run[0][0]
        if len(run) == 5:
            found.append((run[0][1], run[0][0], run[0][2], run[-1][0]))
        i += len(run)  # six or more evenly spaced rules: lined paper, a ruled table
    joined: set[int] = set()
    for n in range(len(found) - 1):
        a, b = found[n], found[n + 1]
        x0, x1 = int(max(a[0], b[0])) - 2, int(min(a[2], b[2])) + 3
        y0, y1 = int(a[3]) + 2, int(b[1]) - 1
        height = a[3] - a[1]
        if x1 - x0 < 0.5 * (a[2] - a[0]) or y1 <= y0 or y1 - y0 > 6 * height:
            continue
        between = dark[y0:y1, max(0, x0):x1]
        if between.size and float(between.mean(axis=0).max()) >= 0.95:
            joined.add(n)
    return [(x0 / scale, y0 / scale, x1 / scale, y1 / scale) for x0, y0, x1, y1 in found], joined


def group_examples(staves: list[BBox], page: PageAnalysis, joined: set[int] | None = None,
                   cores_out: list[BBox] | None = None) -> list[BBox]:
    """One box per example: the staves of one system (a piano's two, a score's several) are one example, and each
    line of music on a page is its own, so each gets a reading short enough to follow. `joined` says which staves
    (top to bottom) are joined to the next by a bar line; without it, staves close together are taken as joined.
    The box takes in what is drawn around the staves (notes on ledger lines, the brace and clefs, chord names,
    words under the notes) but stops at a paragraph of ordinary text and halfway to the next example.
    `cores_out` receives, for each example, the box of its staves alone (first line of the top one to last line of
    the bottom one)."""
    if not staves:
        return []
    groups: list[list[BBox]] = []
    for n, s in enumerate(staves if joined is not None else sorted(staves, key=lambda s: s[1])):
        height = s[3] - s[1]
        last = groups[-1][-1] if groups else None
        if joined is not None:
            together = n - 1 in joined
        else:
            together = bool(last and s[1] - last[3] <= 3.0 * height
                            and min(s[2], last[2]) - max(s[0], last[0]) > 0.5 * (s[2] - s[0]))
        if last and together:
            groups[-1].append(s)
        else:
            groups.append([s])
    cores = [(min(s[0] for s in g), g[0][1], max(s[2] for s in g), g[-1][3]) for g in groups]
    if cores_out is not None:
        cores_out.extend(cores)
    out = []
    for n, (core, g) in enumerate(zip(cores, groups)):
        height = g[0][3] - g[0][1]
        top, bottom = core[1] - 2.5 * height, core[3] + 2.5 * height
        left, right = core[0] - 1.2 * height, core[2] + 0.6 * height
        if n > 0:
            top = max(top, (cores[n - 1][3] + core[1]) / 2)
        if n + 1 < len(cores):
            bottom = min(bottom, (core[3] + cores[n + 1][1]) / 2)
        for blk in page.blocks:
            b = blk.bbox
            if b[2] <= left or b[0] >= right or not _is_words(blk.text):
                continue
            # Right beside the staff, words belong to the music (what is sung, a tempo); further out, a
            # sentence, a heading or a caption is where the example stops
            reach = 1.2 * height if not _is_prose(blk.text) else 0.0
            if b[3] <= core[1] - reach:
                top = max(top, b[3])
            elif b[1] >= core[3] + reach:
                bottom = min(bottom, b[1])
        out.append((max(0.0, left), max(0.0, top), min(page.width, right), min(page.height, bottom)))
    return out


def _is_words(text: str) -> bool:
    """Two or more real words (not note heads from a music font, a chord name, a bar number)."""
    return sum(1 for w in (text or "").split() if sum(c.isalpha() for c in w) >= 3) >= 2


def _is_prose(text: str) -> bool:
    """A sentence, not a lyric line, a chord name or a row of analysis symbols: five or more ordinary words."""
    words = [w for w in (text or "").split() if sum(c.isalpha() for c in w) >= 2]
    return len(words) >= 5 and not all(len(w.strip("-–,.")) <= 3 for w in words)


def add_to_page(page: PageAnalysis, boxes: list[BBox], ids: list[str] | None = None) -> list[ImageRegion]:
    """Each example becomes one figure. What the analysis made of its pieces goes: the note heads read as
    meaningless paragraphs, and the staff lines and other pictures inside it (they're tagged with the figure)."""
    made = []
    for i, b in enumerate(boxes):
        region = ImageRegion(id=ids[i] if ids else f"p{page.number}m{i}", page=page.number, bbox=b, xref=0,
                             kind="music", holds_pieces=True)
        made.append(region)

    def inside(bbox: BBox) -> bool:
        area = bbox_area(bbox)
        if area <= 0:  # a hairline: by its middle
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)
        return any(bbox_intersection(bbox, b) >= 0.8 * area for b in boxes)

    def gone(blk) -> bool:
        # A music font's characters claim boxes much taller than their ink (they reach far above the staff): a
        # block of them with no words belongs to the example its foot stands in
        return inside(blk.bbox) or (not _has_word(blk.text) and in_music(boxes, blk.bbox) is not None)

    page.blocks = [blk for blk in page.blocks if not gone(blk)]
    page.images = [img for img in page.images if not inside(img.bbox)] + made
    return made


def _has_word(text: str) -> bool:
    return any(sum(c.isalpha() for c in w) >= 2 for w in (text or "").split())


def in_music(boxes: list[BBox], bbox: BBox) -> int | None:
    """Which example (by index) a piece of text stands in: the one holding the middle of its foot."""
    cx, foot = (bbox[0] + bbox[2]) / 2, bbox[3] - 0.5
    for n, b in enumerate(boxes):
        if b[0] <= cx <= b[2] and b[1] + 1.0 <= foot <= b[3]:
            return n
    return None


# --------------------------------------------------------------------------- one figure per measure
def find_bar_lines(dark, scale: float, core: BBox) -> list[float]:
    """Where the bar lines of a line of music are (x, in points): the columns that are dark all the way from the
    top line of its top staff to the bottom line of its bottom one. A double bar is one place."""
    import numpy as np

    x0, y0, x1, y1 = (int(round(v * scale)) for v in core)
    left = max(0, x0 - 3)
    strip = dark[y0:y1 + 1, left:x1 + 4]
    if not strip.size:
        return []
    full = strip.mean(axis=0) >= 0.97
    # A bar line ends at the staff's outer lines. A note's stem that happens to be as tall as the staff carries
    # on past one of them, or has its note head beside it there: ink just outside the staff rules the column out
    pad = max(2, int(round(0.12 * (y1 - y0))))
    above = dark[max(0, y0 - pad):max(0, y0 - 1), left:x1 + 4]
    below = dark[y1 + 2:y1 + 1 + pad, left:x1 + 4]
    for outside in (above, below):
        if outside.size:
            inked = outside.any(axis=0)
            near = inked.copy()
            near[1:] |= inked[:-1]
            near[:-1] |= inked[1:]
            full &= ~near
    cols = np.nonzero(full)[0]
    out: list[list[int]] = []
    for c in cols:
        if out and c - out[-1][-1] <= 6:
            out[-1].append(int(c))
        else:
            out.append([int(c)])
    return [(left + (g[0] + g[-1]) / 2) / scale for g in out]


def split_measures(box: BBox, core: BBox, widths: list[float], bars: list[float]) -> list[BBox] | None:
    """One box per measure of a line of music, left to right: cut at the bar lines found on the page, but only when
    they are where the reading service's measures say they should be (it and the page agree on how many measures
    there are and roughly how wide each is). None when they don't: the line then stays one figure."""
    n = len(widths)
    total = sum(widths)
    span = core[2] - core[0]
    if n < 2 or total <= 0 or span <= 0:
        return None
    edge = min(6.0, 0.03 * span)
    inner = [b for b in bars if core[0] + edge < b < core[2] - edge]
    if len(inner) != n - 1:
        return None
    reach = max(6.0, 0.05 * span)
    at = 0.0
    for k, bar in enumerate(inner):
        at += widths[k]
        if abs(core[0] + at / total * span - bar) > reach:
            return None
    cuts = [box[0]] + inner + [box[2]]
    return [(cuts[k], box[1], cuts[k + 1], box[3]) for k in range(n)]


def measure_alts(reading: dict, file: str | None) -> list[str]:
    """What each measure of a line says: the first names the clefs, key and time, and where the line's notation
    is; each says which measure of the line it is."""
    laid = reading["by_measure"]
    n = len(laid)
    out = []
    for k, m in enumerate(laid, 1):
        text = m["text"] or "rests only"
        if len(text) > _FULL_READING_CHARS:
            text = _shorten(text, _FULL_READING_CHARS, ", ") + ", and more"
        alt = (f"{reading['head']}. Measure 1 of {n} on this line: {text}." if k == 1
               else f"Music notation, measure {k} of {n} on this line: {text}.")
        if k == 1 and reading.get("marks"):
            alt += f" Marked: {', '.join(reading['marks'])}."
        if m["chords"]:
            alt += f" Chord names: {', '.join(m['chords'][:12])}."
        if m["words"]:
            alt += f" Words: {_shorten(m['words'], 200, ' ')}"
        if k == 1 and file:
            alt += f" The line's notation is in the document's music files, as {file}."
        out.append(alt)
    return out


# --------------------------------------------------------------------------- reading MusicXML
_KEYS = {0: "no sharps or flats", **{n: f"{n} sharp{'s' if n > 1 else ''}" for n in range(1, 8)},
         **{-n: f"{n} flat{'s' if n > 1 else ''}" for n in range(1, 8)}}
_CLEFS = {("G", "2"): "treble", ("F", "4"): "bass", ("C", "3"): "alto", ("C", "4"): "tenor", ("percussion", None): "percussion"}
_ALTER = {"1": "-sharp ", "-1": "-flat ", "2": "-double-sharp ", "-2": "-double-flat "}
_CHORD_KINDS = {"major": "", "minor": "m", "dominant": "7", "major-seventh": "maj7", "minor-seventh": "m7",
                "diminished": "dim", "augmented": "aug", "suspended-fourth": "sus4"}
_LENGTHS = {"16th": "sixteenth", "32nd": "thirty-second", "64th": "sixty-fourth", "breve": "double whole"}


def _musicxml(data: bytes) -> ET.Element:
    """The score from a .mxl (zipped) or plain MusicXML file."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if not n.startswith("META-INF") and n.lower().endswith((".xml", ".musicxml")))
            data = z.read(name)
    return ET.fromstring(data)


def _note(el: ET.Element) -> str | None:
    length = el.findtext("type") or ""
    length = _LENGTHS.get(length, length)
    if el.find("dot") is not None and length:
        length = f"dotted {length}"
    if el.find("rest") is not None:
        return f"{length} rest".strip()
    pitch = el.find("pitch")
    if pitch is None:
        return None
    name = f"{pitch.findtext('step')}{_ALTER.get(pitch.findtext('alter') or '', '')}{pitch.findtext('octave')}"
    return f"{length} note {name}" if length else name


def _shorten(text: str, limit: int, at: str) -> str:
    """The text up to the last `at` (a space, a comma between notes) before the limit: never cut inside a word."""
    if len(text) <= limit:
        return text
    cut = text.rfind(at, 0, limit + 1)
    return text[:cut if cut > 0 else limit].rstrip(" ,;")


def describe(data: bytes) -> dict | None:
    """A reading of a score: {"alt": words for a screen reader, "measures", "notes", "full": every note is in it}.
    None when there are no notes to read."""
    try:
        root = _musicxml(data)
    except (ET.ParseError, zipfile.BadZipFile, StopIteration, KeyError):
        return None
    clefs: list[str] = []
    key = time = None
    measures: list[str] = []
    notes = 0
    words: list[str] = []
    marks: list[str] = []
    chords: list[str] = []
    # Every measure as it stands on the page, rests and all, with its width: for giving each its own figure
    laid_out: list[dict] = []
    parts = root.findall("part")
    one_line = len(parts) == 1
    for part in parts:
        staff_clef: dict[str, str] = {}
        for index, m in enumerate(part.findall("measure")):
            by_staff: dict[str, list[str]] = {}
            chord: list[str] = []
            chord_staff = "1"
            words_before, chords_before = len(words), len(chords)
            if index and any(pr.get("new-system") == "yes" or pr.get("new-page") == "yes" for pr in m.findall("print")):
                one_line = False  # the service saw more than one line of music in the picture

            def flush() -> None:
                if chord:
                    by_staff.setdefault(chord_staff, []).append(
                        chord[0] if len(chord) == 1 else "chord of " + ", ".join(chord))
                    chord.clear()
            for el in m:
                if el.tag == "attributes":
                    for c in el.findall("clef"):
                        name = _CLEFS.get((c.findtext("sign"), c.findtext("line")), f"{c.findtext('sign')} clef")
                        staff_clef[c.get("number") or "1"] = name
                        if name not in clefs:
                            clefs.append(name)
                    fifths = el.findtext("key/fifths")
                    if fifths is not None and key is None:
                        key = _KEYS.get(int(fifths), fifths)
                    t = el.find("time")
                    if t is not None and time is None and t.findtext("beats"):
                        time = {"common": "common time (4/4)", "cut": "cut time (2/2)"}.get(
                            t.get("symbol") or "", f"{t.findtext('beats')}/{t.findtext('beat-type')}")
                elif el.tag == "direction":
                    for w in el.findall("direction-type/words"):
                        text = " ".join((w.text or "").split())
                        # A tempo or expression mark; a caption the service read along with the music isn't one
                        if text and len(text.split()) <= 3 and text not in marks:
                            marks.append(text)
                elif el.tag == "harmony":
                    step = el.findtext("root/root-step")
                    if step:
                        kind = el.find("kind")
                        suffix = (kind.get("text") if kind is not None and kind.get("text") is not None
                                  else _CHORD_KINDS.get((kind.text or "").strip() if kind is not None else "", ""))
                        chords.append(step + {"1": "♯", "-1": "♭"}.get(el.findtext("root/root-alter") or "", "") + suffix)
                elif el.tag == "note" and el.find("grace") is None:
                    text = _note(el)
                    if text is None:
                        continue
                    if el.find("chord") is None:
                        flush()
                        chord_staff = el.findtext("staff") or "1"
                    chord.append(text)
                    if el.find("rest") is None:
                        notes += 1
                    for lyric in el.findall("lyric"):
                        syllable = (lyric.findtext("text") or "").strip()
                        if syllable:
                            words.append(syllable + ("" if lyric.findtext("syllabic") in ("begin", "middle") else " "))
            flush()
            width = m.get("width")
            placed = {"text": None, "width": float(width) if width and width.replace(".", "", 1).isdigit() else None,
                      "chords": chords[chords_before:], "words": "".join(words[words_before:]).strip()}
            laid_out.append(placed)
            if not by_staff:
                continue
            if all(v == ["rest"] or all(x.endswith("rest") for x in v) for v in by_staff.values()):
                continue  # a measure of rests only: the service pads the staves it couldn't pair up
            if len(by_staff) == 1:
                body = ", ".join(next(iter(by_staff.values())))
            else:
                body = "; ".join(f"{staff_clef.get(s, f'staff {s}')}: " + ", ".join(v) for s, v in sorted(by_staff.items()))
            measures.append(body)
            placed["text"] = body
    if not notes:
        return None
    head = "Music notation"
    if clefs:
        head += f", {' and '.join(clefs)} clef"
    if key:
        head += f", key signature {key}"
    if time:
        head += f", {time}"
    lyric_text = "".join(words).strip()
    count = f"{len(measures)} measure{'s' if len(measures) != 1 else ''}"
    body = " ".join(f"Measure {n}: {m}." for n, m in enumerate(measures, 1)) if len(measures) > 1 else measures[0] + "."
    full = len(measures) <= _FULL_READING_MEASURES and len(body) <= _FULL_READING_CHARS
    if full:
        alt = f"{head}. {body}"
    else:
        # As many whole measures from the start as fit; never part of one, and never part of a word
        opening, shown = "", 0
        for n, m in enumerate(measures, 1):
            piece = f"Measure {n}: {m}."
            if opening and len(opening) + 1 + len(piece) > _FULL_READING_CHARS:
                break
            opening = f"{opening} {piece}".strip()
            shown = n
            if len(opening) > _FULL_READING_CHARS:
                break
        if len(opening) > _FULL_READING_CHARS:  # one very long measure: up to the last whole note that fits
            opening = _shorten(opening, _FULL_READING_CHARS, ", ") + ", and more."
        rest = len(measures) - shown
        alt = (f"{head}, {count}. It begins: {opening}"
               + (f" The other {rest} measure{'s are' if rest != 1 else ' is'} not read out here." if rest > 0 else ""))
    if marks:
        alt += f" Marked: {', '.join(marks[:4])}."
    if chords:
        alt += f" Chord names: {', '.join(chords[:24])}."
    if lyric_text:
        alt += f" Words: {_shorten(lyric_text, 300, ' ')}" + ("…" if len(lyric_text) > 300 else "")
    return {"alt": alt, "measures": len(measures), "notes": notes, "full": full, "head": head, "marks": marks[:4],
            # One line of music, measure by measure as it stands on the page (None: more than one line or part,
            # or no widths to place the measures by)
            "by_measure": laid_out if one_line and len(laid_out) > 1 and all(x["width"] for x in laid_out) else None}


# --------------------------------------------------------------------------- the pipeline step
def apply(pdf_path, analysis, records: list[dict], files: list[tuple[str, bytes]] | None = None,
          score: list[tuple[str, bytes]] | None = None, notes: list[str] | None = None, limits=None) -> int:
    """Find the music examples in the document, make each a figure, and (with the reading service) give it a
    reading as alt text. Appends one record per example: {"page", "bbox", "read": True/False, "measures", "notes",
    "full", "error", "file"}, and to `files` the notation of each example that was read, as (name, MusicXML): what
    someone who reads music opens in notation software to hear it, check it or turn it into braille music.
    When the document is sheet music (most of its pages are music), `score` receives the whole piece read as one:
    (name, MusicXML), one file or one per movement.
    `notes` receives a line for the report when there was more music than the limit allows.
    Returns the number of examples. Blocking: call it in a thread."""
    from app.config import get_settings
    from app.pipeline import omr
    from app.pipeline.analyzer import render_png
    from app.pipeline.structure import heuristic_reading_order

    settings = get_settings()
    if not settings.music_enabled:
        return 0
    regions: list[ImageRegion] = []
    hosts: dict[str, ImageRegion] = {}   # a line of music that is part of a picture -> that picture
    cores: dict[str, BBox] = {}
    rasters: dict[int, list] = {}
    doc = fitz.open(pdf_path)
    try:
        found: dict[int, tuple] = {}
        for page in analysis.pages:
            if page.is_scanned:
                continue
            raster: list = []
            try:
                staves, joined = find_systems(doc[page.number], raster)
            except Exception as exc:  # noqa: BLE001 - a page that won't render has no music we can read
                log.warning("music: page %d not searched: %s", page.number + 1, exc)
                continue
            if staves:
                found[page.number] = (staves, joined, raster)
        # Sheet music, not a document with music in it: more of it is read, and the whole piece as one file too
        score_pages = sheet_music_pages(analysis, {n: (v[0], v[1]) for n, v in found.items()})
        limit = settings.music_max_score_lines if score_pages else settings.music_max_examples
        left_out, first_left_out = 0, None
        for page in analysis.pages:
            if page.number not in found:
                continue
            if len(regions) >= limit:
                left_out += len(found[page.number][0]) - len(found[page.number][1])
                first_left_out = first_left_out if first_left_out is not None else page.number + 1
                continue
            staves, joined, raster = found[page.number]
            page_cores: list[BBox] = []
            boxes = group_examples(staves, page, joined, page_cores)
            if len(boxes) > limit - len(regions):  # the limit falls partway down this page
                left_out += len(boxes) - (limit - len(regions))
                first_left_out = first_left_out if first_left_out is not None else page.number + 1
                boxes = boxes[:limit - len(regions)]
            # Music that is a picture (a scanned score placed on the page) can't be cut into figures: the picture
            # is one piece of content. Its lines are still read; the readings go to the picture
            # (judged by the staves themselves: the room left around them for notes may run past the picture)
            inside = {n: host for n in range(len(boxes)) if (host := _picture_holding(page, page_cores[n])) is not None}
            for n, b in enumerate(boxes):
                if n in inside:
                    line = ImageRegion(id=f"p{page.number}m{n}", page=page.number, bbox=b, xref=0, kind="music")
                    hosts[line.id] = inside[n]
                    regions.append(line)
            own = [n for n in range(len(boxes)) if n not in inside]
            made = add_to_page(page, [boxes[n] for n in own], [f"p{page.number}m{n}" for n in own])
            page.reading_order = heuristic_reading_order(page)
            regions += made
            cores.update({r.id: page_cores[n] for r, n in zip(made, own)})
            rasters[page.number] = raster
        regions.sort(key=lambda r: (r.page, r.bbox[1], r.bbox[0]))
        if left_out and limits is not None:
            limits.limit_hit("MUSIC_MAX_SCORE_LINES" if score_pages else "MUSIC_MAX_EXAMPLES", limit, limit + left_out,
                             "lines of music were left as they were: not made figures, not read")
        if left_out and notes is not None:
            notes.append(f"{left_out} line(s) of music beyond the limit of {limit} "
                         f"({'MUSIC_MAX_SCORE_LINES' if score_pages else 'MUSIC_MAX_EXAMPLES'}) were left as they were, "
                         f"from page {first_left_out} on: not made figures, not read")
        if not regions:
            return 0
        answers: list[dict] = [{"ok": False, "error": "the reading service is off"} for _ in regions]
        if settings.omr_enabled:
            pictures = [render_png(doc, r.page, dpi=300, clip=r.bbox, max_px=4000) for r in regions]
            answers = omr.read_all(pictures)
            if score_pages and score is not None and settings.music_whole_score and any(a.get("ok") for a in answers):
                score.extend(_whole_score(doc, score_pages, settings))
    finally:
        doc.close()
    on_page: dict[int, int] = {}
    in_pictures: dict[int, tuple[ImageRegion, list]] = {}
    for region, answer in zip(regions, answers):
        record = {"page": region.page, "bbox": region.bbox, "read": False}
        on_page[region.page] = n = on_page.get(region.page, 0) + 1
        reading = describe(answer["musicxml"]) if answer.get("ok") else None
        if reading:
            data = answer["musicxml"]
            name = f"page-{region.page + 1:03d}_example-{n}.{'mxl' if data[:2] == b'PK' else 'musicxml'}"
            if files is not None:
                files.append((name, data))
            file = name if files is not None else None
            if region.id in hosts:
                in_pictures.setdefault(id(hosts[region.id]), (hosts[region.id], []))[1].append((reading, file))
                record.update(read=True, measures=reading["measures"], notes=reading["notes"], full=False,
                              alt=reading["alt"], file=file, figures=0)
                records.append(record)
                continue
            split = _split_line(analysis.pages[region.page], region, cores.get(region.id), rasters.get(region.page),
                                reading, file) if settings.music_per_measure else 0
            if not split:
                if file and not reading["full"]:
                    reading["alt"] += f" All of it is in the document's music files, as {name}."
                region.alt = reading["alt"]
            record.update(read=True, measures=reading["measures"], notes=reading["notes"],
                          full=bool(split) or reading["full"], alt=reading["alt"], file=file, figures=split or 1)
        else:
            record["error"] = answer.get("error") or "no notes were found in it"
        records.append(record)
    for picture, lines in in_pictures.values():
        picture.kind = "music"
        picture.alt = _picture_alt(lines)
    return len(regions)


def sheet_music_pages(analysis, found: dict[int, tuple[list[BBox], set[int]]]) -> list[int]:
    """The pages of music (0-based, in order) when the document is sheet music: most of its pages hold two or more
    lines of music and hardly any ordinary text. Empty for a document that merely has music in it (a textbook's
    examples), where there is no whole piece to read."""
    pages = []
    for page in analysis.pages:
        staves, joined = found.get(page.number, ([], set()))
        lines = len(staves) - len(joined)
        if lines >= 2 and sum(1 for b in page.blocks if _is_prose(b.text)) <= 2:
            pages.append(page.number)
    return pages if pages and len(pages) >= 0.6 * len(analysis.pages) else []


def _whole_score(doc: fitz.Document, pages: list[int], settings) -> list[tuple[str, bytes]]:
    """The pages of music, read as one piece by the reading service."""
    from app.pipeline import omr

    part = fitz.open()
    try:
        for n in pages:
            part.insert_pdf(doc, from_page=n, to_page=n)
        data = part.tobytes(garbage=3, deflate=True)
    finally:
        part.close()
    if len(data) > settings.music_whole_score_max_mb * 1024 * 1024:
        log.warning("music: the score is too large to read as one piece (%d MB)", len(data) // (1024 * 1024))
        return []
    files = omr.read_score(data, len(pages))
    if len(files) == 1:
        ext = "mxl" if files[0][1][:2] == b"PK" else "musicxml"
        return [(f"whole-piece.{ext}", files[0][1])]
    return [(f"whole-piece_{n}.{'mxl' if d[:2] == b'PK' else 'musicxml'}", d) for n, (_, d) in enumerate(files, 1)]


def _picture_holding(page: PageAnalysis, box: BBox) -> ImageRegion | None:
    """The picture this line of music is part of, if it is one (most of the line lies in a picture bigger than it)."""
    area = bbox_area(box)
    for img in page.images:
        if img.decorative or img.holds_pieces or not img.xref:
            continue
        if bbox_area(img.bbox) > area and bbox_intersection(box, img.bbox) >= 0.8 * area:
            return img
    return None


def _picture_alt(lines: list[tuple[dict, str | None]]) -> str:
    """One alt text for a picture of music: how much there is, how its first line begins, and where the rest is."""
    first, file = lines[0]
    measures = sum(r["measures"] for r, _ in lines)
    alt = (f"{first['head']}: a picture of {len(lines)} line{'s' if len(lines) != 1 else ''} of music, "
           f"{measures} measures in all.")
    opening = next((m["text"] for m in (first.get("by_measure") or []) if m["text"]), None)
    if opening:
        alt += f" It begins: {_shorten(opening, 500, ', ')}."
    names = [f for _, f in lines if f]
    if names:
        alt += (f" The notation of each line is in the document's music files, {names[0]}"
                + (f" to {names[-1]}." if len(names) > 1 else "."))
    return alt


def _split_line(page: PageAnalysis, region: ImageRegion, core: BBox | None, raster: list | None, reading: dict,
                file: str | None) -> int:
    """Give each measure of a line of music its own figure, in place of the line's one, when the page's bar lines
    agree with what the service read. Returns how many figures the line became (0: left as one)."""
    laid = reading.get("by_measure")
    if not laid or core is None or not raster:
        return 0
    try:
        bars = find_bar_lines(raster[0], raster[1], core)
    except Exception as exc:  # noqa: BLE001 - no bar lines, no split
        log.warning("music: bar lines not found on page %d: %s", page.number + 1, exc)
        return 0
    boxes = split_measures(region.bbox, core, [m["width"] for m in laid], bars)
    if not boxes:
        return 0
    alts = measure_alts(reading, file)
    made = [ImageRegion(id=f"{region.id}x{k}", page=page.number, bbox=b, xref=0, kind="music", holds_pieces=True, alt=alt)
            for k, (b, alt) in enumerate(zip(boxes, alts), 1)]
    page.images = [i for i in page.images if i is not region] + made
    # Read left to right where the line was: side-by-side figures mustn't be taken for columns
    order: list[str] = []
    for eid in page.reading_order:
        order += [m.id for m in made] if eid == region.id else [eid]
    page.reading_order = order
    return len(made)


def carry_over(analysis, redone) -> None:
    """A second analysis of the document (the copy with some pages flattened) gets the same music figures, with
    their readings, as the first: on a flattened page there are no staves left to find, and on the others a
    second search would only repeat the first. They keep the order they had among themselves (a line's measures
    side by side read left to right, not as columns)."""
    from app.pipeline.structure import heuristic_reading_order

    for first in analysis.pages:
        examples = [i for i in first.images if i.kind == "music" and not i.decorative]
        if not examples or first.number >= len(redone.pages):
            continue
        page = redone.pages[first.number]
        for made, was in zip(add_to_page(page, [i.bbox for i in examples], [i.id for i in examples]), examples):
            made.alt, made.latex = was.alt, was.latex
        page.reading_order = heuristic_reading_order(page)
        ids = {i.id for i in examples}
        in_order = iter([e for e in first.reading_order if e in ids])
        page.reading_order = [next(in_order, e) if e in ids else e for e in page.reading_order]


def write_archive(path, files: list[tuple[str, bytes]], document: str) -> None:
    """The examples' notation in one zip, with a note on what the files are and how far to trust them."""
    whole = [name for name, _ in files if name.startswith("whole-piece")]
    note = (f"Music notation read from {document}\n\n"
            + ((f"{' and '.join(whole)}: the whole piece as one score" + (" per movement" if len(whole) > 1 else "")
                + ". Start here.\n\n") if whole else "")
            + "Each page-… file is one music example from the document (one line of music, or one short excerpt), "
            "named by its page and its place on the page.\n\n"
            "The files are MusicXML: open them in notation software (MuseScore is free) to see the music, play it "
            "back, or convert it to braille music.\n\n"
            "The notation was read by software (Audiveris), not by a person. It is usually close but can have wrong "
            "notes, rhythms or clefs: play it back against the page before relying on it.\n")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", note)
        for name, data in files:
            z.writestr(name, data)
