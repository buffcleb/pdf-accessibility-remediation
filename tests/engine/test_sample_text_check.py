"""scripts/sample_text_check.py: a sentence copied from a sample document into a tracked file is found, by file and
line, without the sentence being printed. Tests once held sentences from the regression documents, which would have
gone out with a public snapshot."""
import importlib.util
from pathlib import Path

import pymupdf as fitz

_spec = importlib.util.spec_from_file_location(
    "sample_text_check", Path(__file__).resolve().parents[2] / "scripts" / "sample_text_check.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

SENTENCE = "The heron stood in the shallow water until the tide turned."


def _samples(tmp_path):
    folder = tmp_path / "samples"
    folder.mkdir()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), SENTENCE, fontsize=11)
    page.insert_text((72, 130), "1 2 3 4 5 6 7 8 9 10 11 12", fontsize=11)
    doc.save(folder / "birds.pdf")
    doc = fitz.open()
    doc.new_page()   # a scan: nothing to read
    doc.save(folder / "scan.pdf")
    return folder


def test_a_copied_sentence_is_found_and_never_printed(tmp_path, capsys):
    samples, tree = _samples(tmp_path), tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_x.py").write_text(f'import x\n\ntext = "{SENTENCE}"\n')
    (tree / "notes.md").write_text("The heron is a bird.\nThe tide turned at noon.\n")   # the same words, not the sentence
    (tree / "blob.bin").write_bytes(b"\xff\xfe\x00")

    assert check.main(["check", str(tree), str(samples)]) == 1
    out, err = capsys.readouterr()
    assert out.splitlines() == [f"tests/test_x.py:3: shares a run of {check.RUN_LENGTH} words with birds.pdf"]
    assert "heron" not in out + err
    assert "scan.pdf" in err   # what couldn't be compared is said


def test_numbers_in_a_row_and_a_clean_tree_are_not_reported(tmp_path, capsys):
    samples, tree = _samples(tmp_path), tmp_path / "tree"
    tree.mkdir()
    (tree / "table.py").write_text("order = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]\n")
    assert check.main(["check", str(tree), str(samples)]) == 0
    assert capsys.readouterr().out == ""


def test_a_sentence_broken_over_two_lines_is_still_found(tmp_path):
    samples, tree = _samples(tmp_path), tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("Quoted: The heron stood in the\nshallow water until the tide turned.\n")
    seen, unread = check.sample_runs(samples)
    assert unread == ["scan.pdf"]
    assert [(f, n) for f, n, _ in check.shared(tree, seen)][0] == ("a.md", 1)
