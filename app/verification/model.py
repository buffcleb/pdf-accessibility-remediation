"""Findings produced by the verification stage."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Severity ladder. "error" only comes from Layer 1 and fails the job.
SEVERITY_ORDER = {"error": 0, "high": 1, "review": 2, "suggest": 3, "warning": 4}


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
    tagmap: dict | None = None  # for the tag viewer; written to its own file, not into the report

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
