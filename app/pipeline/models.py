"""Data model shared by the analysis, AI, and tagging stages.

All bounding boxes are (x0, y0, x1, y1) in PyMuPDF page space (origin top-left,
unrotated page). Each PageAnalysis carries the matrix needed to map PDF user space
into that space, so the content-stream tagger can match operators to regions.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

BBox = tuple[float, float, float, float]


def bbox_area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def bbox_intersection(a: BBox, b: BBox) -> float:
    return bbox_area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def bbox_union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def bbox_contains_point(b: BBox, x: float, y: float, tol: float = 0.0) -> bool:
    return b[0] - tol <= x <= b[2] + tol and b[1] - tol <= y <= b[3] + tol


def point_distance(b: BBox, x: float, y: float) -> float:
    dx = max(b[0] - x, 0.0, x - b[2])
    dy = max(b[1] - y, 0.0, y - b[3])
    return (dx * dx + dy * dy) ** 0.5


_STATEMENT_MATH = set("=×÷±≤≥<>+−√∑∫∞≈≠·^")
_STATEMENT_WORD = re.compile(r"[A-Za-z]{3,}")
_CONTINUES = re.compile(r"(?:[=+\-−×÷·*/<>≤≥≈≠(,:]|\b(?:and|or|so|then|where|if|because|gives|is|of|to))\s*$",
                        re.IGNORECASE)
_CONTINUATION_START = re.compile(r"^\s*[=+\-−×÷·*/<>≤≥≈≠)]")


def _math_like(visible: str) -> bool:
    """Mostly math: an equals sign, and few words (the rule that makes a paragraph a Formula)."""
    dense = len(re.sub(r"\s", "", visible)) or 1
    words = sum(len(w) for w in _STATEMENT_WORD.findall(visible))
    return "=" in visible and any(c in _STATEMENT_MATH for c in visible) and words <= 0.25 * dense


def join_statements(parts: list[tuple[str, str]]) -> str:
    """Join pieces of read-aloud text, each (spoken, visible). Where one complete equation ends and the
    next begins ("10³ = 1000" then "10⁻³ = 0.001"), put "; " so screen readers pause between them.
    Anything that continues a statement (a piece starting with "=" or an operator, or the previous one
    ending with an operator, a comma or a joining word like "and") is joined with a space, and so is
    prose."""
    out, current = "", ""
    for spoken, visible in parts:
        spoken, visible = spoken.strip(), visible.strip()
        if not spoken:
            continue
        if (out and _math_like(current) and _math_like(visible) and not _CONTINUES.search(current)
                and not _CONTINUES.search(out) and not _CONTINUATION_START.match(visible)):
            out += "; " + spoken
            current = visible
        else:
            out += (" " if out else "") + spoken
            current += (" " if current else "") + visible
    return out


@dataclass
class TextBlock:
    id: str
    page: int
    bbox: BBox
    text: str
    font_size: float
    bold: bool = False
    role: str = "P"  # P, H1..H6, LI, Caption, Artifact
    list_label: str | None = None
    artifact_type: str | None = None  # Pagination / Layout when role == Artifact
    # What a screen reader should hear when the content order or layout doesn't read correctly on its
    # own: math typeset piece by piece, superscripts ("10 to the power of 4"), subscripts.
    actual_text: str | None = None
    has_scripts: bool = False  # contains superscripts or subscripts
    # Baseline of the first line: where a row of text sits, even when something in it (a stacked fraction,
    # an exponent) reaches higher than the rest of the line
    baseline: float | None = None


def append_block(dst: "TextBlock", src: "TextBlock") -> None:
    """Join src onto the end of dst (paragraph lines, list item continuations)."""
    spoken = join_statements([(dst.actual_text or dst.text, dst.text), (src.actual_text or src.text, src.text)])
    dst.text = f"{dst.text} {src.text}"
    if dst.actual_text or src.actual_text or spoken != dst.text:
        dst.actual_text = spoken
    dst.bbox = bbox_union([dst.bbox, src.bbox])
    dst.has_scripts = dst.has_scripts or src.has_scripts


@dataclass
class ImageRegion:
    id: str
    page: int
    bbox: BBox
    xref: int
    width: int = 0
    height: int = 0
    alt: str | None = None
    decorative: bool = False
    kind: str = "image"  # image, formula, chart, music, handwriting, ...
    latex: str | None = None


@dataclass
class TableCell:
    row: int
    col: int
    bbox: BBox | None
    text: str
    is_header: bool = False
    row_span: int = 1
    col_span: int = 1
    actual_text: str | None = None  # spoken form when the cell holds math (fractions, exponents)

    @property
    def key(self) -> str:
        return f"r{self.row}c{self.col}"


@dataclass
class TableRegion:
    id: str
    page: int
    bbox: BBox
    n_rows: int
    n_cols: int
    cells: list[TableCell] = field(default_factory=list)
    header_rows: int = 1
    header_cols: int = 0
    summary: str | None = None
    irregular: bool = False
    ai_used: bool = False
    header_evidence: bool = False  # layout strongly suggests row 0 is a header row

    def cell_target_id(self, cell: TableCell) -> str:
        return f"{self.id}:{cell.key}"

    def normalized_cells(self) -> list[TableCell]:
        """Cells whose spans tile the grid exactly (PDF/UA 7.2-43: every row covers the same columns).
        Spans are clamped where they overlap or overflow; uncovered positions get empty cells."""
        occupied: set[tuple[int, int]] = set()
        # Positions where another real cell starts: a span may never grow over them, so no
        # cell (and none of its tagged content) is ever dropped.
        origins = {(c.row, c.col) for c in self.cells}
        out: list[TableCell] = []
        for c in sorted(self.cells, key=lambda c: (c.row, c.col)):
            if c.row >= self.n_rows or c.col >= self.n_cols:
                continue
            if (c.row, c.col) in occupied:
                # Already covered by an earlier span: move it to the next free column in its row.
                free = next((k for k in range(self.n_cols) if (c.row, k) not in occupied), None)
                if free is None:
                    continue
                c.col = free
            blocked = lambda r, k: (r, k) in occupied or ((r, k) in origins and (r, k) != (c.row, c.col))  # noqa: E731
            cs = 1
            while cs < c.col_span and c.col + cs < self.n_cols and not blocked(c.row, c.col + cs):
                cs += 1
            rs = 1
            while rs < c.row_span and c.row + rs < self.n_rows and not any(
                    blocked(c.row + rs, c.col + k) for k in range(cs)):
                rs += 1
            c.col_span, c.row_span = cs, rs
            occupied |= {(c.row + i, c.col + k) for i in range(rs) for k in range(cs)}
            out.append(c)
        for r in range(self.n_rows):
            for col in range(self.n_cols):
                if (r, col) not in occupied:
                    out.append(TableCell(row=r, col=col, bbox=None, text="",
                                         is_header=r < self.header_rows or col < self.header_cols))
                    occupied.add((r, col))
        return sorted(out, key=lambda c: (c.row, c.col))


@dataclass
class PageAnalysis:
    number: int  # 0-based
    width: float
    height: float
    rotation: int
    pdf_to_page: tuple[float, float, float, float, float, float]  # PDF user space -> PyMuPDF space
    blocks: list[TextBlock] = field(default_factory=list)
    images: list[ImageRegion] = field(default_factory=list)
    tables: list[TableRegion] = field(default_factory=list)
    is_scanned: bool = False
    columns: int = 1
    reading_order: list[str] = field(default_factory=list)  # ids of blocks/images/tables
    reading_order_source: str = "heuristic"
    font_issues: list[str] = field(default_factory=list)

    def element(self, eid: str) -> TextBlock | ImageRegion | TableRegion | None:
        for coll in (self.blocks, self.images, self.tables):
            for e in coll:
                if e.id == eid:
                    return e
        return None


@dataclass
class DocumentAnalysis:
    page_count: int
    pages: list[PageAnalysis]
    title: str | None
    language: str | None
    has_struct_tree: bool
    is_marked: bool
    body_font_size: float = 10.0
    font_issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
