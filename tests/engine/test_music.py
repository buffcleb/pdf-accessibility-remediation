"""Music notation: each example is found by its staves and becomes one figure; a reading service turns it into
words. Found on a mathematics-of-music textbook whose note heads were tagged as paragraphs of nothing, mixed into
the sentences around them."""
import base64
import json
import threading

import fakeredis
import pymupdf as fitz
import pytest

from app.config import get_settings
from app.pipeline import music, omr
from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.tagmap import build_tagmap
from tests.engine.layout import block, document, page
from tests.engine.pdfs import integrity_errors


def _staff(p, x0, x1, top, gap=5.0, lines=5):
    for n in range(lines):
        p.draw_line((x0, top + n * gap), (x1, top + n * gap), width=0.4)


def _page(tmp_path, draw, name="m.pdf"):
    doc = fitz.open()
    draw(doc.new_page(width=612, height=792))
    doc.save(tmp_path / name)
    return tmp_path / name


def _staves(path):
    with fitz.open(path) as doc:
        return music.find_staves(doc[0])


def test_five_evenly_spaced_rules_are_a_staff(tmp_path):
    staves = _staves(_page(tmp_path, lambda p: _staff(p, 150, 450, 300)))
    assert len(staves) == 1
    x0, y0, x1, y1 = staves[0]
    assert abs(x0 - 150) < 2 and abs(x1 - 450) < 2 and abs(y0 - 300) < 1.5 and abs(y1 - 320) < 1.5


def test_lined_paper_a_ruled_table_and_short_marks_are_not_staves(tmp_path):
    def draw(p):
        _staff(p, 100, 500, 100, gap=5.0, lines=8)      # eight close rules: lined paper
        _staff(p, 100, 500, 300, gap=16.0, lines=5)     # table rows
        _staff(p, 100, 130, 500)                        # too short to be an example
        for n in range(5):                              # five rules of different lengths
            p.draw_line((100, 600 + n * 5), (300 + n * 30, 600 + n * 5), width=0.4)
    assert _staves(_page(tmp_path, draw)) == []


def _analysis(blocks=()):
    return page(*(block(f"b{n}", bbox, text, size=10) for n, (bbox, text) in enumerate(blocks)))


def test_staves_joined_by_a_brace_are_one_example_and_distant_ones_are_separate():
    grand = [(150, 300, 450, 320), (150, 345, 450, 365)]   # treble over bass
    far = [(150, 520, 450, 540)]
    boxes = music.group_examples(grand + far, _analysis())
    assert len(boxes) == 2
    assert boxes[0][1] < 300 and boxes[0][3] > 365 and boxes[0][3] <= boxes[1][1]  # room for notes; no overlap


def test_an_example_stops_at_a_sentence_but_keeps_the_words_beside_it():
    page = _analysis([((100, 262, 500, 274), "Below are a few short examples, picked without a plan."),
                      ((160, 284, 260, 294), "Andante"),     # tempo, right above the staff
                      ((160, 326, 400, 336), "The mor - ning comes"),      # what is sung
                      ((100, 352, 400, 362), "(ii) A. Composer, Suite no. 2, Minuet, bars 9–10.")])
    (box,) = music.group_examples([(150, 300, 450, 320)], page)
    assert 274 <= box[1] <= 284 and 336 <= box[3] <= 352


def test_note_heads_with_tall_boxes_belong_to_the_example_they_stand_in():
    boxes = [(130, 250, 460, 370)]
    assert music.in_music(boxes, (156, 113, 162, 333)) == 0     # a music font's box, reaching far above
    assert music.in_music(boxes, (156, 113, 162, 240)) is None  # text that ends above the example
    page = _analysis([((156, 113, 162, 333), "�"), ((100, 100, 500, 112), "A sentence above the example.")])
    music.add_to_page(page, boxes)
    assert [b.text for b in page.blocks] == ["A sentence above the example."]
    assert [(i.kind, i.bbox) for i in page.images] == [("music", boxes[0])]


_XML = """<score-partwise><part id="P1">
<measure number="1"><attributes><key><fifths>-2</fifths></key><time symbol="cut"><beats>2</beats><beat-type>2</beat-type></time>
 <clef number="1"><sign>G</sign><line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef></attributes>
 <note><pitch><step>F</step><octave>5</octave></pitch><type>quarter</type><dot/><staff>1</staff></note>
 <note><pitch><step>D</step><octave>5</octave></pitch><type>eighth</type><staff>1</staff></note>
 <note><rest/><type>quarter</type><staff>2</staff></note>
 <note><pitch><step>B</step><alter>-1</alter><octave>3</octave></pitch><type>quarter</type><staff>2</staff></note>
 <note><chord/><pitch><step>D</step><octave>4</octave></pitch><type>quarter</type><staff>2</staff></note></measure>
<measure number="2"><note><rest/><type>whole</type><staff>1</staff></note><note><rest/><type>whole</type><staff>2</staff></note></measure>
<measure number="3"><direction><direction-type><words>Gently</words></direction-type></direction>
 <direction><direction-type><words>(iii) A caption the service read along with the music.</words></direction-type></direction>
 <harmony><root><root-step>D</root-step></root><kind text="m7">minor-seventh</kind></harmony>
 <harmony><root><root-step>B</root-step><root-alter>-1</root-alter></root><kind>major</kind></harmony>
 <note><pitch><step>C</step><alter>1</alter><octave>4</octave></pitch><type>16th</type><staff>1</staff>
 <lyric><syllabic>begin</syllabic><text>mor</text></lyric></note>
 <note><pitch><step>C</step><octave>4</octave></pitch><type>half</type><staff>1</staff><lyric><syllabic>end</syllabic><text>ning</text></lyric></note></measure>
</part></score-partwise>"""


def test_a_score_is_read_out_measure_by_measure():
    got = music.describe(_XML.encode())
    assert got["measures"] == 2 and got["notes"] == 6 and got["full"]  # the measure of rests isn't a measure to read
    assert got["alt"] == (
        "Music notation, treble and bass clef, key signature 2 flats, cut time (2/2). "
        "Measure 1: treble: dotted quarter note F5, eighth note D5; bass: quarter rest, "
        "chord of quarter note B-flat 3, quarter note D4. "
        "Measure 2: sixteenth note C-sharp 4, half note C4. Marked: Gently. Chord names: Dm7, B♭. Words: morning")


def test_a_long_score_gets_a_summary_and_its_opening():
    measure = ('<measure><note><pitch><step>C</step><octave>4</octave></pitch><type>quarter</type></note></measure>')
    got = music.describe(f"<score-partwise><part>{measure * 12}</part></score-partwise>".encode())
    assert not got["full"] and got["measures"] == 12
    assert got["alt"].startswith("Music notation, 12 measures. It begins: Measure 1: quarter note C4. Measure 2:")
    assert "Measure 12: quarter note C4." in got["alt"]  # short measures: all of them fit


def test_a_shortened_reading_stops_at_a_whole_measure_never_inside_a_word():
    """A page of piano music: each measure is long. The reading stopped at a fixed number of characters, in the
    middle of a word ("...eighth note C5, eigh")."""
    note = '<note><pitch><step>E</step><alter>-1</alter><octave>4</octave></pitch><type>eighth</type></note>'
    got = music.describe(f"<score-partwise><part>{('<measure>' + note * 16 + '</measure>') * 10}</part></score-partwise>".encode())
    alt = got["alt"]
    assert not got["full"] and alt.startswith("Music notation, 10 measures. It begins: Measure 1: eighth note E-flat 4,")
    opening = alt.split("It begins: ")[1].split(" The other ")[0]
    assert opening.endswith("eighth note E-flat 4.") and len(opening) <= 1200
    shown = opening.count("Measure ")
    assert 1 <= shown < 10 and alt.endswith(f"The other {10 - shown} measures are not read out here.")
    # One measure too long to fit: it stops at a whole note
    long = music.describe(f"<score-partwise><part><measure>{note * 80}</measure></part></score-partwise>".encode())
    assert long["alt"].endswith("eighth note E-flat 4, and more.") and len(long["alt"]) < 1300


def test_nothing_to_read_is_no_reading():
    assert music.describe(b"not xml") is None
    assert music.describe(b"<score-partwise><part><measure><note><rest/></note></measure></part></score-partwise>") is None


# --------------------------------------------------------------------------- the reading service's queue
@pytest.fixture
def queue(monkeypatch):
    client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(get_settings(), "omr_timeout_s", 2, raising=False)
    return client


def _serve(client, answers):
    """Stand in for the service: answer each request as it comes."""
    def run():
        for answer in answers:
            got = client.brpop([omr.QUEUE], timeout=5)
            if not got:
                return
            req = json.loads(got[1])
            assert base64.b64decode(req["png"]).startswith(b"PNG")
            client.lpush(omr.RESULT.format(id=req["id"]), json.dumps(answer))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_readings_come_back_in_the_order_asked(queue):
    xml = base64.b64encode(_XML.encode()).decode()
    t = _serve(queue, [{"ok": True, "musicxml": xml}, {"ok": False, "error": "No system found"}])
    got = omr.read_all([b"PNG one", b"PNG two"], client=queue)
    t.join(5)
    assert got[0] == {"ok": True, "musicxml": _XML.encode()}
    assert got[1] == {"ok": False, "error": "No system found"}


def test_a_service_that_never_answers_costs_one_wait_and_leaves_nothing_queued(queue):
    got = omr.read_all([b"PNG one", b"PNG two", b"PNG three"], client=queue)
    assert [g["ok"] for g in got] == [False, False, False]
    assert queue.llen(omr.QUEUE) == 0


# --------------------------------------------------------------------------- through the tagger
def test_an_example_is_tagged_as_one_figure_with_its_reading(tmp_path, monkeypatch):
    def draw(p):
        p.insert_text((100, 200), "Here is a short example of a scale going up.", fontsize=11, fontname="helv")
        _staff(p, 150, 450, 300)
        for n in range(6):  # "notes": marks on the staff
            p.insert_text((180 + n * 40, 318 - n * 2.5), "o", fontsize=9, fontname="helv")
        p.insert_text((100, 420), "The text carries on below the example.", fontsize=11, fontname="helv")
    src = _page(tmp_path, draw)
    settings = get_settings()
    monkeypatch.setattr(settings, "omr_enabled", True, raising=False)
    monkeypatch.setattr(omr, "read_all", lambda pictures: [{"ok": True, "musicxml": _XML.encode()} for _ in pictures])
    analysis = prepare_analysis(src)
    records: list[dict] = []
    assert music.apply(src, analysis, records) == 1
    assert records[0]["read"] and records[0]["measures"] == 2
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, analysis, title="Example", language="en-US")
    assert integrity_errors(dst) == []
    tm = build_tagmap(dst)
    figures = [e for e in tm["elements"] if e["role"] == "Figure"]
    assert len(figures) == 1 and figures[0]["alt"].startswith("Music notation, treble and bass clef")
    inside = [p for p in tm["pieces"] if p["el"] == figures[0]["i"]]
    assert len([p for p in inside if p["kind"] == "text"]) == 6   # the notes are in the figure, not paragraphs
    paragraphs = [e["text"] for e in tm["elements"] if e["role"] == "P"]
    assert len(paragraphs) == 2 and all("o" != t.strip() for t in paragraphs)


def test_without_the_service_the_example_is_still_one_figure(tmp_path):
    src = _page(tmp_path, lambda p: _staff(p, 150, 450, 300))
    analysis = prepare_analysis(src)
    records: list[dict] = []
    assert music.apply(src, analysis, records) == 1
    assert records == [{"page": 0, "bbox": analysis.pages[0].images[-1].bbox, "read": False,
                        "error": "the reading service is off"}]
    assert analysis.pages[0].images[-1].kind == "music" and analysis.pages[0].images[-1].alt is None


def test_a_second_analysis_gets_the_same_examples_and_readings(tmp_path):
    """The copy with flattened pages is analyzed again: its music examples are the first analysis's, readings
    included, not whatever a second look at a redrawn page would find."""
    src = _page(tmp_path, lambda p: _staff(p, 150, 450, 300))
    first = prepare_analysis(src)
    music.apply(src, first, [])
    first.pages[0].images[-1].alt = "Music notation, treble clef. half note C4."
    redone = prepare_analysis(src)
    assert not [i for i in redone.pages[0].images if i.kind == "music"]
    music.carry_over(first, redone)
    (example,) = [i for i in redone.pages[0].images if i.kind == "music"]
    assert example.alt == "Music notation, treble clef. half note C4." and example.bbox == first.pages[0].images[-1].bbox
    assert example.id in redone.pages[0].reading_order


# --------------------------------------------------------------------------- lines of music, and their files
def _score(p):
    """Two lines of piano music: in each, a bar line joins the two staves; nothing joins one line to the next."""
    for top in (200, 300):
        _staff(p, 100, 500, top)
        _staff(p, 100, 500, top + 40)
        for x in (100, 300, 500):
            p.draw_line((x, top), (x, top + 60), width=0.8)


def test_each_line_of_music_is_its_own_example(tmp_path):
    src = _page(tmp_path, _score)
    with fitz.open(src) as doc:
        staves, joined = music.find_systems(doc[0])
    assert len(staves) == 4 and joined == {0, 2}          # the piano's two staves, twice; the lines aren't joined
    boxes = music.group_examples(staves, _analysis(), joined)
    assert len(boxes) == 2 and boxes[0][3] <= boxes[1][1]
    # Told nothing about bar lines, staves this close would be taken as one example (the older rule, still the
    # fallback): the bar lines are what tells a system from a page
    assert len(music.group_examples(staves, _analysis())) == 1


def test_the_notation_that_was_read_is_kept_as_files(tmp_path, monkeypatch):
    import zipfile

    src = _page(tmp_path, _score)
    monkeypatch.setattr(get_settings(), "omr_enabled", True, raising=False)
    answers = iter([{"ok": True, "musicxml": _XML.encode()}, {"ok": False, "error": "no staves were found in the picture"}])
    monkeypatch.setattr(omr, "read_all", lambda pictures: [next(answers) for _ in pictures])
    analysis = prepare_analysis(src)
    records: list[dict] = []
    files: list[tuple[str, bytes]] = []
    assert music.apply(src, analysis, records, files) == 2
    assert [name for name, _ in files] == ["page-001_example-1.musicxml"] and files[0][1] == _XML.encode()
    assert records[0]["file"] == "page-001_example-1.musicxml" and "file" not in records[1]
    music.write_archive(tmp_path / "music.zip", files, "score.pdf")
    with zipfile.ZipFile(tmp_path / "music.zip") as z:
        assert z.namelist() == ["README.txt", "page-001_example-1.musicxml"]
        assert "read by software" in z.read("README.txt").decode() and "score.pdf" in z.read("README.txt").decode()


# --------------------------------------------------------------------------- one figure per measure
def _line_xml(widths, rest_at=None):
    note = '<note><pitch><step>C</step><octave>4</octave></pitch><type>quarter</type><staff>1</staff></note>'
    rest = '<note><rest/><type>whole</type><staff>1</staff></note>'
    first = ('<attributes><key><fifths>1</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time>'
             '<clef number="1"><sign>G</sign><line>2</line></clef></attributes>')
    measures = "".join(
        f'<measure number="{n}" width="{w}">{first if n == 1 else ""}{rest if n == rest_at else note * n}</measure>'
        for n, w in enumerate(widths, 1))
    return f"<score-partwise><part id='P1'>{measures}</part></score-partwise>".encode()


def test_the_reader_also_gives_each_measure_as_it_stands_on_the_page():
    got = music.describe(_line_xml([300, 200, 200], rest_at=2))
    assert got["measures"] == 2                               # the reading leaves a measure of rests out
    laid = got["by_measure"]                                  # ...but on the page it is there, between the others
    assert [m["width"] for m in laid] == [300.0, 200.0, 200.0]
    assert [m["text"] for m in laid] == ["quarter note C4", None, "quarter note C4, quarter note C4, quarter note C4"]
    alts = music.measure_alts(got, "page-001_example-1.mxl")
    assert alts[0] == ("Music notation, treble clef, key signature 1 sharp, 4/4. Measure 1 of 3 on this line: "
                       "quarter note C4. The line's notation is in the document's music files, as page-001_example-1.mxl.")
    assert alts[1] == "Music notation, measure 2 of 3 on this line: rests only."
    # More than one line of music in what was read, or no widths: nothing to place measures by
    two = _line_xml([300, 200]).replace(b'<measure number="2" width="200">', b'<measure number="2" width="200"><print new-system="yes"/>')
    assert music.describe(two)["by_measure"] is None
    assert music.describe(_XML.encode())["by_measure"] is None


def test_a_line_is_cut_at_its_bar_lines_only_when_the_page_and_the_reading_agree():
    box, core = (88, 250, 506, 370), (100, 300, 500, 320)
    bars = [100.0, 220.0, 300.0, 400.0, 500.0]               # the system's own start and end, and three between
    cut = music.split_measures(box, core, [300, 200, 250, 250], bars)
    assert [(round(b[0]), round(b[2])) for b in cut] == [(88, 220), (220, 300), (300, 400), (400, 506)]
    assert all(b[1] == 250 and b[3] == 370 for b in cut)
    assert music.split_measures(box, core, [300, 200, 500], bars) is None          # three measures read, four on the page
    assert music.split_measures(box, core, [100, 100, 400, 400], bars) is None     # four, but not where the page has them
    assert music.split_measures(box, core, [1000], bars) is None                   # one measure: nothing to cut


def _barred(p, bars=(100, 220, 300, 400, 500)):
    _staff(p, 100, 500, 300)
    for x in bars:
        p.draw_line((x, 300), (x, 320), width=0.8)
    p.draw_line((150, 304), (150, 318), width=0.6)   # a note's stem: not as tall as the staff


def test_bar_lines_are_found_on_the_page_and_stems_are_not(tmp_path):
    src = _page(tmp_path, _barred)
    with fitz.open(src) as doc:
        raster: list = []
        staves, _ = music.find_systems(doc[0], raster)
    bars = music.find_bar_lines(raster[0], raster[1], staves[0])
    assert [round(b) for b in bars] == [100, 220, 300, 400, 500]


def test_each_measure_becomes_a_figure_read_left_to_right(tmp_path, monkeypatch):
    def draw(p):
        p.insert_text((100, 200), "Here is a line of music with four measures in it.", fontsize=11, fontname="helv")
        _barred(p)
        p.insert_text((100, 420), "The text carries on below the example.", fontsize=11, fontname="helv")
    src = _page(tmp_path, draw)
    monkeypatch.setattr(get_settings(), "omr_enabled", True, raising=False)
    monkeypatch.setattr(omr, "read_all", lambda pictures: [{"ok": True, "musicxml": _line_xml([300, 200, 250, 250])}])
    analysis = prepare_analysis(src)
    records: list[dict] = []
    files: list = []
    assert music.apply(src, analysis, records, files) == 1 and records[0]["figures"] == 4 and records[0]["full"]
    page = analysis.pages[0]
    figures = [i for i in page.images if i.kind == "music"]
    assert [round(i.bbox[0]) for i in figures][1:] == [220, 300, 400] and figures[0].bbox[0] < 100  # cut at the bar lines
    assert len(files) == 1                                        # still one file per line
    order = [e for e in page.reading_order if e in {i.id for i in figures}]
    assert order == [i.id for i in figures]                       # left to right, between the two sentences
    texts = [getattr(page.element(e), "text", None) for e in page.reading_order]
    assert texts[0].startswith("Here is a line") and texts[-1].startswith("The text carries on")
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, analysis, title="Line", language="en-US")
    assert integrity_errors(dst) == []
    tagged = [e["alt"] for e in build_tagmap(dst)["elements"] if e["role"] == "Figure"]
    assert len(tagged) == 4 and [("easure %d of 4" % n) in a for n, a in enumerate(tagged, 1)] == [True] * 4
    # A second analysis keeps the measures in that order
    again = prepare_analysis(src)
    music.carry_over(analysis, again)
    assert [e for e in again.pages[0].reading_order if e.endswith(("x1", "x2", "x3", "x4"))] == order


def test_a_line_whose_measures_dont_match_the_page_stays_one_figure(tmp_path, monkeypatch):
    src = _page(tmp_path, _barred)
    monkeypatch.setattr(get_settings(), "omr_enabled", True, raising=False)
    monkeypatch.setattr(omr, "read_all", lambda pictures: [{"ok": True, "musicxml": _line_xml([400, 300, 300])}])
    analysis = prepare_analysis(src)
    records: list[dict] = []
    assert music.apply(src, analysis, records, []) == 1 and records[0]["figures"] == 1
    (figure,) = [i for i in analysis.pages[0].images if i.kind == "music"]
    assert figure.alt.startswith("Music notation, treble clef") and "Measure 3:" in figure.alt


def test_music_that_is_a_picture_is_read_and_stays_one_figure(tmp_path, monkeypatch):
    """A scanned score placed on a page is one piece of content: it can't be cut into lines or measures. Its lines
    are still read, and the picture's alt text says what is there and where the notation is."""
    score = fitz.open()
    sp = score.new_page(width=400, height=160)
    for top in (30, 100):
        _staff(sp, 20, 380, top)
        for x in (20, 200, 380):
            sp.draw_line((x, top), (x, top + 20), width=0.8)
    pix = sp.get_pixmap(dpi=200)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 150), "The duet below is a scan of the printed score.", fontsize=11, fontname="helv")
    page.insert_image(fitz.Rect(100, 200, 500, 360), pixmap=pix)
    doc.save(tmp_path / "scan.pdf")
    monkeypatch.setattr(get_settings(), "omr_enabled", True, raising=False)
    monkeypatch.setattr(omr, "read_all", lambda pictures: [{"ok": True, "musicxml": _line_xml([300, 300])} for _ in pictures])
    analysis = prepare_analysis(tmp_path / "scan.pdf")
    records: list[dict] = []
    files: list = []
    assert music.apply(tmp_path / "scan.pdf", analysis, records, files) == 2
    figures = [i for i in analysis.pages[0].images if not i.decorative]
    assert len(figures) == 1 and figures[0].xref and figures[0].kind == "music"     # the picture itself, nothing added
    assert figures[0].alt == (
        "Music notation, treble clef, key signature 1 sharp, 4/4: a picture of 2 lines of music, 4 measures in all. "
        "It begins: quarter note C4. The notation of each line is in the document's music files, "
        "page-001_example-1.musicxml to page-001_example-2.musicxml.")
    assert [r["figures"] for r in records] == [0, 0] and len(files) == 2


# --------------------------------------------------------------------------- sheet music: the whole piece
def _doc_analysis(pages):
    return document(*pages)


def _numbered(n, blocks=()):
    page = _analysis(blocks)
    page.number = n
    return page


def test_sheet_music_is_told_from_a_document_with_music_in_it():
    line = [(100, 100, 500, 120), (100, 140, 500, 160)]
    two_lines = (line + [(100, 300, 500, 320), (100, 340, 500, 360)], {0, 2})     # two piano lines
    prose = [((100, 500 + 14 * k, 500, 512 + 14 * k), "A paragraph of ordinary text about the music above it.") for k in range(4)]
    # A piece: a title page and three pages of music
    piece = _doc_analysis([_numbered(0, [((100, 100, 500, 130), "Capriccio")]), _numbered(1), _numbered(2), _numbered(3)])
    assert music.sheet_music_pages(piece, {1: two_lines, 2: two_lines, 3: two_lines}) == [1, 2, 3]
    # A textbook: music on some pages, among paragraphs; and a page with one example isn't a page of music
    book = _doc_analysis([_numbered(n, prose) for n in range(5)])
    assert music.sheet_music_pages(book, {1: two_lines, 3: (line, {0})}) == []
    mostly_text = _doc_analysis([_numbered(0), _numbered(1), _numbered(2), _numbered(3)])
    assert music.sheet_music_pages(mostly_text, {0: two_lines}) == []


def test_a_whole_score_is_asked_for_once_and_can_come_back_as_several_files(queue):
    def serve():
        got = queue.brpop([omr.QUEUE], timeout=5)
        req = json.loads(got[1])
        assert base64.b64decode(req["pdf"]).startswith(b"%PDF") and req["pages"] == 3 and "png" not in req
        queue.lpush(omr.RESULT.format(id=req["id"]), json.dumps({"ok": True, "files": [
            {"name": "music.mvt1.mxl", "musicxml": base64.b64encode(b"PK one").decode()},
            {"name": "music.mvt2.mxl", "musicxml": base64.b64encode(b"PK two").decode()}]}))
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    assert omr.read_score(b"%PDF-1.7 score", 3, client=queue) == [("music.mvt1.mxl", b"PK one"), ("music.mvt2.mxl", b"PK two")]
    t.join(5)


def test_a_service_that_only_takes_pictures_gives_no_whole_score(queue, monkeypatch):
    monkeypatch.setattr(get_settings(), "omr_page_timeout_s", 0, raising=False)

    def serve():  # an older service: it answers, but can't do this
        got = queue.brpop([omr.QUEUE], timeout=5)
        queue.lpush(omr.RESULT.format(id=json.loads(got[1])["id"]), json.dumps({"ok": False, "error": "KeyError: 'png'"}))
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    assert omr.read_score(b"%PDF-1.7 score", 1, client=queue) == []
    t.join(5)
    assert omr.read_score(b"%PDF-1.7 score", 1, client=queue) == [] and queue.llen(omr.QUEUE) == 0   # nobody there


def test_sheet_music_gets_the_whole_piece_as_one_file_beside_its_lines(tmp_path, monkeypatch):
    import zipfile

    doc = fitz.open()
    for _ in range(2):
        _score(doc.new_page(width=612, height=792))
    doc.save(tmp_path / "piece.pdf")
    monkeypatch.setattr(get_settings(), "omr_enabled", True, raising=False)
    monkeypatch.setattr(omr, "read_all", lambda pictures: [{"ok": True, "musicxml": _line_xml([300, 300])} for _ in pictures])
    asked = []
    monkeypatch.setattr(omr, "read_score", lambda pdf, pages: asked.append((pdf[:4], pages)) or [("piece.mxl", b"PK whole")])
    analysis = prepare_analysis(tmp_path / "piece.pdf")
    files, score = [], []
    assert music.apply(tmp_path / "piece.pdf", analysis, [], files, score) == 4      # two lines on each page
    assert asked == [(b"%PDF", 2)] and score == [("whole-piece.mxl", b"PK whole")] and len(files) == 4
    music.write_archive(tmp_path / "m.zip", score + files, "piece.pdf")
    with zipfile.ZipFile(tmp_path / "m.zip") as z:
        assert z.namelist()[:2] == ["README.txt", "whole-piece.mxl"] and len(z.namelist()) == 6
        assert "whole-piece.mxl: the whole piece as one score. Start here." in z.read("README.txt").decode()


def test_a_document_with_one_example_is_not_asked_for_a_whole_score(tmp_path, monkeypatch):
    src = _page(tmp_path, _barred)
    monkeypatch.setattr(get_settings(), "omr_enabled", True, raising=False)
    monkeypatch.setattr(omr, "read_all", lambda pictures: [{"ok": True, "musicxml": _line_xml([300, 200, 250, 250])}])
    monkeypatch.setattr(omr, "read_score", lambda pdf, pages: pytest.fail("not sheet music: nothing to read as a piece"))
    score: list = []
    assert music.apply(src, prepare_analysis(src), [], [], score) == 1 and score == []


def test_the_summary_says_how_the_music_was_tagged():
    from app.pipeline.orchestrator import music_summary

    one = {"read": True, "figures": 1}
    assert music_summary([one]) == "Music notation: 1 line or example of music, each tagged as one figure"
    assert music_summary([{"read": False}] * 3) == "Music notation: 3 lines or examples of music, each tagged as one figure"
    split = [{"read": True, "figures": 4}] * 9 + [one]
    assert music_summary(split) == ("Music notation: 10 lines or examples of music, tagged as 37 figures (9 of them one "
                                    "figure per measure; the others one figure each)")
    scan = [{"read": True, "figures": 0}] * 12
    assert music_summary(scan) == ("Music notation: 12 lines or examples of music, 12 are part of a picture (a scanned "
                                   "score), which stays one figure")
    assert music_summary(scan + [one]).endswith("each tagged as one figure; 12 are part of a picture (a scanned score), "
                                                "which stays one figure")
