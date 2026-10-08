"""Test harness: submit a directory of PDFs to the API, poll until done, assert on the results.

    python harness/run_harness.py samples/ --api http://localhost:8000 --provider mock
    python harness/run_harness.py samples/ --provider ollama --model llava:13b --base64
    python harness/run_harness.py samples/ --provider none --markdown     # also write Markdown reports

Exit code 0 only if every assertion passed for every file.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import httpx


class Check:
    def __init__(self, name: str):
        self.name = name
        self.failures: list[str] = []

    def that(self, cond: bool, msg: str) -> None:
        if not cond:
            self.failures.append(msg)


def fetch(client: httpx.Client, ref: str | None, as_base64: bool) -> bytes | None:
    if not ref:
        return None
    if as_base64:
        return base64.b64decode(ref)
    r = client.get(ref)
    r.raise_for_status()
    return r.content


def run_one(client: httpx.Client, pdf: Path, args, out_dir: Path) -> Check:
    chk = Check(pdf.name)
    flags = {"debug": args.debug, "report_only": args.report_only, "return_base64": args.base64}
    with pdf.open("rb") as fh:
        data = {"ai_provider": args.provider, "flags": json.dumps(flags)}
        if args.model:
            data["ai_model"] = args.model
        if args.route != "standard":
            data["route"] = args.route
        r = client.post("/api/v1/remediate", files={"file": (pdf.name, fh, "application/pdf")}, data=data)
    chk.that(r.status_code == 202, f"submit returned {r.status_code}: {r.text[:200]}")
    if r.status_code != 202:
        return chk
    job = r.json()
    chk.that(job.get("status") == "processing" and job.get("job_id"), f"unexpected submit body {job}")
    job_id = job["job_id"]

    deadline = time.monotonic() + args.timeout
    body: dict = {}
    while time.monotonic() < deadline:
        try:
            body = client.get(f"/api/v1/jobs/{job_id}").json()
        except httpx.TransportError as exc:  # API restarting: keep polling until the deadline
            print(f"   (API unreachable: {exc}; retrying)", flush=True)
            time.sleep(max(args.poll, 5))
            continue
        if body.get("status") in ("completed", "failed"):
            break
        time.sleep(args.poll)
    else:
        chk.that(False, f"timed out after {args.timeout}s waiting for job {job_id}")
    (out_dir / f"{pdf.stem}_job.json").write_text(json.dumps(body, indent=2)[:5_000_000])

    if args.markdown:
        md = client.get(f"/api/v1/jobs/{job_id}", params={"format": "markdown"})
        chk.that(md.status_code == 200 and md.text.startswith("# "), f"markdown report unavailable ({md.status_code})")
        if md.status_code == 200:
            # Make API-relative links absolute so the saved file works outside the API.
            text = md.text.replace("](/api/", f"]({args.api.rstrip('/')}/api/")
            (out_dir / f"{pdf.stem}_report.md").write_text(text)
            chk.markdown = f"{pdf.stem}_report.md"  # type: ignore[attr-defined]

    chk.that(body.get("status") == "completed", f"job ended as {body.get('status')}: {body.get('error')}")
    if body.get("status") != "completed":
        return chk

    m = body.get("metrics") or {}
    chk.that(isinstance(m.get("pages_processed"), int) and m["pages_processed"] > 0, "metrics.pages_processed missing")
    tok = m.get("ai_tokens_used") or {}
    chk.that("prompt_tokens" in tok and "completion_tokens" in tok, "metrics.ai_tokens_used missing")
    if args.provider != "none":
        chk.that(m.get("ai_calls", 0) == 0 or tok.get("prompt_tokens", 0) > 0, "AI calls made but no tokens counted")
    chk.that(isinstance(m.get("processing_time_seconds"), (int, float)), "metrics.processing_time_seconds missing")
    for w in body.get("warnings") or []:
        if "AI call" in w or "AI review did not run" in w:
            chk.that(False, f"AI problem: {w}")

    rep = body.get("reports") or {}
    chk.that(bool(rep.get("remediation_summary")), "remediation_summary empty")
    comp = rep.get("comparison") or {}
    chk.that("before" in comp, "comparison.before missing")
    before_xml = fetch(client, rep.get("verapdf_before"), args.base64)
    chk.that(before_xml is not None and b"<report" in before_xml, "veraPDF 'before' report not returned")

    if args.report_only:
        chk.that((body.get("files") or {}).get("remediated_primary") is None, "report_only returned a file")
        return chk

    chk.that(comp.get("after") is not None, "comparison.after missing")
    after_xml = fetch(client, rep.get("verapdf_compliance_report"), args.base64)
    chk.that(after_xml is not None and b"<report" in after_xml, "veraPDF compliance report not returned")
    before, after = comp.get("before", {}), comp.get("after", {})
    adobe = args.route == "adobe"
    if adobe:
        # Adobe's output is reported as it is: worse results are findings about Adobe, not harness failures.
        a = m.get("adobe") or {}
        chk.that(a.get("pages_sent") == m.get("pages_processed"), f"Adobe pages sent {a.get('pages_sent')} != "
                                                                  f"pages {m.get('pages_processed')}")
        if rep.get("adobe_reports") is not None:
            chk.that(all(fetch(client, x, args.base64) for x in rep["adobe_reports"]), "Adobe report not downloadable")
        tv = (rep.get("tag_verification") or {}).get("primary") or {}
        if (tv.get("integrity") or {}).get("passed") is False:
            print(f"  note: {pdf.name}: Adobe's tags have structural problems (see the tag review)")
    if not adobe and isinstance(before.get("failed_checks"), int) and isinstance(after.get("failed_checks"), int):
        chk.that(after["failed_checks"] <= before["failed_checks"],
                 f"failed checks increased: {before['failed_checks']} -> {after['failed_checks']}")
    chk.that(after.get("tagged") is True, "output is not tagged")
    vf = comp.get("visual_fidelity") or {}
    if adobe:
        if vf.get("identical") is not True:
            print(f"  note: {pdf.name}: Adobe's output renders differently on pages {vf.get('differing_pages')}")
    else:
        chk.that(vf.get("identical") is True, f"non-destructive output not pixel-identical: {vf}")

    files = body.get("files") or {}
    primary = fetch(client, files.get("remediated_primary"), args.base64)
    chk.that(primary is not None and primary.startswith(b"%PDF"), "primary remediated PDF not downloadable")
    if primary:
        (out_dir / f"{pdf.stem}_primary.pdf").write_bytes(primary)
    if files.get("remediated_visual_fallback"):
        fb = fetch(client, files["remediated_visual_fallback"], args.base64)
        chk.that(fb is not None and fb.startswith(b"%PDF"), "visual fallback PDF not downloadable")
        if fb:
            (out_dir / f"{pdf.stem}_visual_fallback.pdf").write_bytes(fb)
    if args.expect_compliant:
        chk.that(after.get("compliant") is True, f"output not compliant: {after.get('verapdf')}")
    chk.summary = (before.get("failed_checks"), after.get("failed_checks"), after.get("compliant"),  # type: ignore[attr-defined]
                   bool(files.get("remediated_visual_fallback")), m.get("processing_time_seconds"))
    return chk


def write_markdown_summary(results: list[Check], args) -> Path:
    lines = [
        "# Remediation harness report", "",
        f"- **API:** {args.api}",
        f"- **AI provider:** {args.provider}" + (f" (`{args.model}`)" if args.model else ""),
        f"- **Mode:** {'report only' if args.report_only else 'remediate'}"
        + (", base64 files" if args.base64 else ""),
        f"- **Run at:** {time.strftime('%Y-%m-%d %H:%M:%S')}", "",
        "| File | Failed checks before | After | PDF/UA-1 | Two files | Seconds | Harness | Report |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in results:
        before, after, compliant, dual, secs = getattr(c, "summary", ("–",) * 5)
        verdict = "✅" if compliant is True else "❌" if compliant is False else "–"
        report = f"[view]({c.markdown})" if getattr(c, "markdown", None) else "–"
        lines.append(f"| {c.name} | {before} | {after} | {verdict} | {'yes' if dual is True else 'no' if dual is False else '–'} "
                     f"| {secs} | {'PASS' if not c.failures else 'FAIL'} | {report} |")
    failed = [c for c in results if c.failures]
    if failed:
        lines += ["", "## Harness failures", ""]
        for c in failed:
            lines += [f"- **{c.name}**"] + [f"  - {f}" for f in c.failures]
    path = args.out / "harness_report.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", type=Path)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--provider", default="none")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base64", action="store_true", help="request base64 inline files instead of URLs")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--route", choices=["standard", "adobe"], default="standard",
                    help="adobe: tag with Adobe Autotag (needs ADOBE_ENABLED on the engine; use the fake Adobe "
                         "server for testing)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--markdown", action="store_true",
                    help="save each job's Markdown report plus a combined harness_report.md")
    ap.add_argument("--expect-compliant", action="store_true", help="fail unless the primary output passes veraPDF")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--poll", type=float, default=2)
    ap.add_argument("--out", type=Path, default=Path("harness_output"))
    args = ap.parse_args()

    pdfs = sorted(p for p in args.directory.glob("*.pdf") if not p.name.startswith("."))
    if not pdfs:
        print(f"no PDFs in {args.directory}")
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    results = []
    with httpx.Client(base_url=args.api, headers=headers, timeout=120) as client:
        health = client.get("/api/v1/health").json()
        print(f"health: {health}")
        for pdf in pdfs:
            print(f"-> {pdf.name}", flush=True)
            results.append(run_one(client, pdf, args, args.out))

    print("\n{:<32} {:>8} {:>8} {:>10} {:>6} {:>8}  {}".format(
        "file", "before", "after", "compliant", "dual", "secs", "result"))
    ok = True
    for c in results:
        s = getattr(c, "summary", ("-",) * 5)
        status = "PASS" if not c.failures else "FAIL"
        ok &= not c.failures
        print("{:<32} {:>8} {:>8} {:>10} {:>6} {:>8}  {}".format(c.name[:32], *(str(x) for x in s), status))
        for f in c.failures:
            print(f"     - {f}")
    failed = sum(1 for c in results if c.failures)
    print(f"\n{'ALL PASSED' if ok else f'{failed} of {len(results)} FAILED'} ({len(results)} files). "
          f"Responses saved to {args.out}/")
    if args.markdown:
        path = write_markdown_summary(results, args)
        print(f"Markdown report: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
