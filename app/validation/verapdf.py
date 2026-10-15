"""veraPDF: the official XML report plus a parsed summary.

Two ways to run it, with the same report:
- the **service** (veraPDF's REST server, `VERAPDF_URL`): one Java process kept running, so a check doesn't pay
  for starting Java (about a second each). Its config must show up to 200 failed checks per rule with their error
  messages (deploy/verapdf/config/validator.xml), as the engine reads pages, fonts and glyph widths from them.
- the **command line** in the engine image (`VERAPDF_PATH`), one Java process per check.

A job can choose (the portal's super-admin setting, sent with the job); without a choice the service is used when
it's configured. If the service doesn't answer, that check falls back to the command line.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx

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
    via: str | None = None  # "service" or "command line"
    # 7.21.5: (font, code) -> (program width, Widths entry), from failed-check details
    width_mismatches: dict = field(default_factory=dict)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("via", None)
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


@dataclass(frozen=True)
class ValidatorOptions:
    service: bool | None = None  # None: the service when it's configured
    ua2: bool = True  # also run the informational flavours (PDF/UA-2), not only the verdict's


_options: contextvars.ContextVar[ValidatorOptions] = contextvars.ContextVar("verapdf_options",
                                                                            default=ValidatorOptions())


@contextlib.contextmanager
def job_validator(flags: dict | None):
    """Run a job's checks the way its flags say: {"validator": "service" | "command_line", "verapdf_ua2": bool}."""
    flags = flags or {}
    mode = flags.get("validator")
    token = _options.set(ValidatorOptions(service=None if mode not in ("service", "command_line") else mode == "service",
                                          ua2=flags.get("verapdf_ua2", True) is not False))
    try:
        yield
    finally:
        _options.reset(token)


def flavours() -> list[str]:
    """The flavours to check: the first gives the verdict; the rest are shown for information, unless turned off."""
    fl = get_settings().flavours
    return fl if _options.get().ua2 else fl[:1]


def _service_url() -> str | None:
    url = get_settings().verapdf_url.rstrip("/")
    want = _options.get().service
    return url if url and want is not False else None


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


@lru_cache
def service_version(url: str) -> str | None:
    try:
        r = httpx.get(f"{url}/api", timeout=10)
        r.raise_for_status()
        return f"veraPDF REST {r.json().get('version', '')}".strip()
    except (httpx.HTTPError, ValueError):
        return None


def _run_service(url: str, pdf_path: Path, flavour: str) -> bytes:
    """The service's XML report; raises httpx.HTTPError when the service can't give one."""
    with open(pdf_path, "rb") as fh:
        r = httpx.post(f"{url}/api/validate/{flavour}", headers={"Accept": "application/xml"},
                       files={"file": (pdf_path.name, fh, "application/pdf")},
                       timeout=get_settings().verapdf_timeout_s)
    r.raise_for_status()
    if not r.content.strip():
        raise httpx.HTTPError("empty report")
    return r.content


def run_verapdf(pdf_path: Path, xml_out: Path, flavour: str = "ua1") -> VeraResult:
    url = _service_url()
    if url:
        started = time.monotonic()
        try:
            xml = _run_service(url, pdf_path, flavour)
        except httpx.HTTPError as exc:
            log.warning("veraPDF service at %s failed (%s); using the command line for %s", url, exc, pdf_path.name)
        else:
            res = parse_report(xml, flavour)
            if res.error is None:
                xml_out.write_bytes(xml)
                res.xml_path, res.via = str(xml_out), "service"
                log.debug("veraPDF service: %s %s in %.2fs", pdf_path.name, flavour, time.monotonic() - started)
                return res
            log.warning("veraPDF service report for %s unusable (%s); using the command line", pdf_path.name, res.error)
    return _run_cli(pdf_path, xml_out, flavour)


def _run_cli(pdf_path: Path, xml_out: Path, flavour: str) -> VeraResult:
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
    res.xml_path, res.via = str(xml_out), "command line"
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
    # In clause order (7.1 before 7.2 before 7.21), whichever way veraPDF ran: the service lists rules in another order
    res.failures.sort(key=_clause_order)
    return res


def _clause_order(f: RuleFailure) -> tuple:
    num = lambda v: int(v) if v.isdigit() else 0  # noqa: E731
    return tuple(num(p) for p in f.clause.split(".")), num(f.test)
