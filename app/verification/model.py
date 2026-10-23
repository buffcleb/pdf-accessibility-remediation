"""Findings produced by the verification stage."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Severity ladder. "error" only comes from Layer 1 and fails the job.
SEVERITY_ORDER = {"error": 0, "high": 1, "review": 2, "suggest": 3, "warning": 4}


MAX_BOXES = 20


def area_boxes(boxes, limit: int = MAX_BOXES) -> list[list[float]]:
    """Glyph or picture boxes gathered into areas a person can see: a line, then a block when there are too many
    lines to list. At most limit areas, rounded."""
    boxes = [tuple(float(v) for v in b) for b in boxes if b[2] > b[0] and b[3] > b[1]]
    if not boxes:
        return []
    gap = max(1.0, sorted(b[3] - b[1] for b in boxes)[len(boxes) // 2] * 0.6)  # about a space, at the usual size
    areas: list[list[float]] = []
    while True:
        areas = []
        for b in sorted(boxes, key=lambda b: (b[1], b[0])):
            for a in areas:
                if b[0] - gap <= a[2] and a[0] - gap <= b[2] and b[1] - gap / 3 <= a[3] and a[1] - gap / 3 <= b[3]:
                    a[:] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    break
            else:
                areas.append(list(b))
        if len(areas) <= limit:
            break
        boxes, gap = [tuple(a) for a in areas], gap * 2  # lines into blocks, blocks into larger ones
    return [[round(v, 1) for v in a] for a in sorted(areas, key=lambda a: (a[1], a[0]))]


@dataclass
class Finding:
    layer: str  # integrity | semantic | ai
    severity: str  # error | high | review | suggest | warning
    issue: str
    page: int | None = None  # 1-based
    role: str | None = None
    element: int | None = None  # pre-order index of the structure element
    text: str | None = None  # snippet of the element's content
    detail: str | None = None
    action: str | None = None
    confidence: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Where on the page it is, in the tag map's page space, for an item no tag holds (#426): at most MAX_BOXES
    boxes: list[list[float]] | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v not in (None, {}, [])}
        return d


@dataclass
class VerificationReport:
    integrity_passed: bool = True
    integrity_checks: int = 0
    elements_checked: int = 0
    findings: list[Finding] = field(default_factory=list)
    ai: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    tagmap: dict | None = None  # for the tag manager; written to its own file, not into the report

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.page or 0, f.element or 0))

    def to_dict(self, limit: int = 500) -> dict:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return {
            "integrity": {
                "passed": self.integrity_passed,
                "checks": self.integrity_checks,
                "failures": [f.to_dict() for f in self.errors][:100],
            },
            "summary": {"elements_checked": self.elements_checked, "flagged": len(self.findings), **counts},
            "findings": [f.to_dict() for f in self.sorted_findings() if f.severity != "error"][:limit],
            "ai": self.ai,
            "skipped": self.skipped,
        }


class IntegrityError(RuntimeError):
    """Raised when an output fails Layer 1; the job must fail."""

    def __init__(self, label: str, failures: list[Finding]):
        self.label = label
        self.failures = failures
        first = "; ".join(f.issue + (f" (page {f.page})" if f.page else "") for f in failures[:5])
        super().__init__(f"tag integrity check failed for the {label} output: {len(failures)} problem(s): {first}")
