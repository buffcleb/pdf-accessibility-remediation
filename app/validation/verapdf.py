"""veraPDF CLI wrapper (subprocess). Produces the official XML report plus a parsed summary."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree

from app.config import get_settings

log = logging.getLogger(__name__)

_PAGE_CTX = re.compile(r"pages\[(\d+)\]")
_GLYPH_CTX = re.compile(r"usedGlyphs\[\d+\]\((\S+) \S+ (\d+) ")
_WIDTH_MSG = re.compile(r"Glyph width (-?[\d.]+) .*?\(value (-?[\d.]+)\)")
_FONT_CTX = re.compile(r"/font\[\d+\]\(([^)]+)\)")


@dataclass
class RuleFailure:
    clause: str
    test: str
    specification: str
    description: str
    failed_checks: int
    pages: list[int] = field(default_factory=list)  # 0-based pages seen in failed-check contexts
    fonts: list[str] = field(default_factory=list)  # font names seen in failed-check contexts


@dataclass
class VeraResult:
    flavour: str
    available: bool
    compliant: bool | None = None
    profile: str | None = None
    passed_rules: int = 0
    failed_rules: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    failures: list[RuleFailure] = field(default_factory=list)
    xml_path: str | None = None
    error: str | None = None
    # 7.21.5: (font, code) -> (program width, Widths entry), from failed-check details
    width_mismatches: dict = field(default_factory=dict)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("xml_path", None)
        d.pop("width_mismatches", None)
        d["failures"] = d["failures"][:60]
        return d

    @property
    def failed_clauses(self) -> set[str]:
        return {f.clause for f in self.failures}

    def pages_failing(self, clause_prefixes: tuple[str, ...]) -> set[int]:
        return {p for f in self.failures if f.clause.startswith(clause_prefixes) for p in f.pages}

    def failed_clauses_with(self, clause_prefixes: tuple[str, ...]) -> set[str]:
        return {f.clause for f in self.failures if f.clause.startswith(clause_prefixes)}

    def failing_fonts(self, clause_prefixes: tuple[str, ...]) -> set[str]:
        return {n for f in self.failures if f.clause.startswith(clause_prefixes) for n in f.fonts}

    def unattributed(self, clause_prefixes: tuple[str, ...]) -> bool:
        """True if a matching failure could not be tied to specific pages."""
        return any(f.clause.startswith(clause_prefixes) and not f.pages for f in self.failures)


def _binary() -> str | None:
    path = get_settings().verapdf_path
    return shutil.which(path) or (path if Path(path).is_file() else None)


@lru_cache
def verapdf_version() -> str | None:
    exe = _binary()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        line = next((ln for ln in out.stdout.splitlines() if "veraPDF" in ln), out.stdout.strip()[:80])
        return line.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_verapdf(pdf_path: Path, xml_out: Path, flavour: str = "ua1") -> VeraResult:
    exe = _binary()
    if not exe:
        return VeraResult(flavour=flavour, available=False, error="veraPDF CLI not found")
    cmd = [exe, "--flavour", flavour, "--format", "xml", "--maxfailuresdisplayed", "200", str(pdf_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=get_settings().verapdf_timeout_s)
    except subprocess.TimeoutExpired:
        return VeraResult(flavour=flavour, available=True, error="veraPDF timed out")
    xml = proc.stdout
    if not xml.strip():
        return VeraResult(flavour=flavour, available=True,
                          error=f"veraPDF produced no output (exit {proc.returncode}): {proc.stderr.decode()[:300]}")
    xml_out.write_bytes(xml)
    res = parse_report(xml, flavour)
    res.xml_path = str(xml_out)
    return res


def parse_report(xml: bytes, flavour: str) -> VeraResult:
    res = VeraResult(flavour=flavour, available=True)
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError as exc:
        res.error = f"unparseable veraPDF report: {exc}"
        return res
    vr = next(iter(root.iter("validationReport")), None)
    if vr is None:
        exc_el = next(iter(root.iter("exceptionMessage")), None)
        res.error = exc_el.text if exc_el is not None else "no validationReport in veraPDF output"
        return res
    res.compliant = vr.get("isCompliant") == "true"
    res.profile = vr.get("profileName")
    details = vr.find("details")
    if details is not None:
        res.passed_rules = int(details.get("passedRules", 0))
        res.failed_rules = int(details.get("failedRules", 0))
        res.passed_checks = int(details.get("passedChecks", 0))
        res.failed_checks = int(details.get("failedChecks", 0))
        for rule in details.iter("rule"):
            if rule.get("status") != "failed":
                continue
            desc = rule.findtext("description") or ""
            pages, fonts = set(), set()
            if rule.get("clause", "").startswith("7.21.5"):
                for check in rule.iter("check"):
                    g = _GLYPH_CTX.search(check.findtext("context") or "")
                    w = _WIDTH_MSG.search(check.findtext("errorMessage") or "")
                    if g and w:
                        res.width_mismatches[(g.group(1), int(g.group(2)))] = (float(w.group(1)), float(w.group(2)))
            for ctx in rule.iter("context"):
                m = _PAGE_CTX.search(ctx.text or "")
                if m:
                    pages.add(int(m.group(1)))
                fonts.update(f.split("+", 1)[-1] for f in _FONT_CTX.findall(ctx.text or ""))
            res.failures.append(
                RuleFailure(
                    clause=rule.get("clause", ""),
                    test=rule.get("testNumber", ""),
                    specification=rule.get("specification", ""),
                    description=desc.strip(),
                    failed_checks=int(rule.get("failedChecks", 0)),
                    pages=sorted(pages),
                    fonts=sorted(fonts),
                )
            )
    return res
