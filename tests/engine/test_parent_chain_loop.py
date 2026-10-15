"""A page with no /Resources whose /Parent chain loops. Three places walk up that chain for the resources (the
content marker, the integrity checker, the glyph-width fixer); each walked for ever on such a file, and the job
held its worker until the time limit."""
import threading

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging.content_marker import ContentMarker, inherited_resources
from app.tagging.font_metrics import fix_width_mismatches
from app.verification.integrity import IntegrityChecker


def _looping_page(path):
    """One page, no /Resources, whose /Parent is a node that names itself as its parent."""
    pdf = pikepdf.new()
    loop = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array(), Count=0))
    loop.Parent = loop
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 200, 100]),
                                        Contents=pdf.make_stream(b"0 0 50 50 re f"), Parent=loop))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    pdf.save(path)
    with pikepdf.open(path) as back:
        assert back.pages[0].obj.Parent.Parent.objgen == back.pages[0].obj.Parent.objgen, "the loop survived saving"


def _returns_in_time(fn, seconds: float = 5.0):
    """Whether fn returns within the time; a walk that never ends is left behind as a daemon thread."""
    done = threading.Event()
    out: list = []

    def run():
        try:
            out.append(fn())
        finally:
            done.set()
    threading.Thread(target=run, daemon=True).start()
    return done.wait(seconds), out


def test_the_resource_walk_stops_at_a_loop(tmp_path):
    _looping_page(tmp_path / "loop.pdf")
    with pikepdf.open(tmp_path / "loop.pdf") as pdf:
        page = pdf.pages[0].obj
        ok, out = _returns_in_time(lambda: inherited_resources(page))
        assert ok and len(out[0]) == 0
        ok, out = _returns_in_time(lambda: IntegrityChecker._resources(page))
        assert ok and len(out[0]) == 0
        marker = ContentMarker(pdf, pdf.pages[0], (1, 0, 0, 1, 0, 0), lambda *a: ("artifact:Layout", "Artifact"))
        ok, out = _returns_in_time(marker.run)
        assert ok and out[0].artifacts == 1


def test_the_width_fixer_stops_at_a_loop(tmp_path):
    _looping_page(tmp_path / "loop.pdf")
    ok, out = _returns_in_time(lambda: fix_width_mismatches(tmp_path / "loop.pdf", tmp_path / "out.pdf", {}))
    assert ok and out[0] == 0
