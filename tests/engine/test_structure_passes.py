"""The structure passes run in an order that says why (refactor plan item 4)."""
import pytest

from app.pipeline import structure
from app.pipeline.structure.headings import NOT_A_HEADING


def test_each_pass_runs_after_the_ones_it_needs():
    names = [fn.__name__ for fn, _ in structure.PASSES]
    for n, (fn, needs) in enumerate(structure.PASSES):
        for need in needs:
            assert need in names[:n], f"{fn.__name__} needs {need} first"


def test_a_pass_out_of_order_is_refused():
    (first, _) = structure.PASSES[0]
    second, needs = next((fn, n) for fn, n in structure.PASSES if fn.__name__ == "assign_headings")
    assert needs  # assign_headings needs the drawn lines marked
    with pytest.raises(RuntimeError, match="needs"):
        structure._check_order([(second, needs), (first, ())])


def test_the_heading_exclusions_are_named_in_order():
    assert [name for name, _ in NOT_A_HEADING] == [
        "too little text", "a sentence", "a sentence carrying on to the next line", "a row of controls or decoration",
        "a contents entry", "a formula in larger print", "a line with a stacked fraction",
        "a list item at body size", "a list item, even in larger print", "a value typed onto a scanned form",
        "read by OCR with little confidence"]
