import { useState } from "react";
import { fmtNum } from "./ui";

// Readable view of a veraPDF machine-readable (XML) report. The XML is untrusted input: it is
// parsed with DOMParser (browsers never fetch external entities) and only ever rendered as text.

type Check = { context: string; message: string };
type Rule = {
  clause: string;
  test: string;
  tags: string;
  description: string;
  failedChecks: number;
  pages: number[];
  checks: Check[];
};
export type VeraReport = {
  profile: string;
  compliant: boolean;
  version: string;
  passedRules: number;
  failedRules: number;
  passedChecks: number;
  failedChecks: number;
  rules: Rule[];
};

const MAX_CHECKS = 5;

function num(el: Element | null, attr: string): number {
  return Number(el?.getAttribute(attr) ?? 0) || 0;
}

export function parseVeraPdf(xml: string): VeraReport | null {
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  if (doc.querySelector("parsererror")) return null;
  const vr = doc.querySelector("validationReport");
  if (!vr) return null;
  const details = vr.querySelector("details");
  const rules: Rule[] = Array.from(vr.querySelectorAll("rule[status='failed']")).map((r) => {
    const checks = Array.from(r.querySelectorAll("check[status='failed']"));
    const pages = new Set<number>();
    for (const c of checks) {
      const m = /pages\[(\d+)\]/.exec(c.querySelector("context")?.textContent ?? "");
      if (m) pages.add(Number(m[1]) + 1);
    }
    return {
      clause: r.getAttribute("clause") ?? "",
      test: r.getAttribute("testNumber") ?? "",
      tags: r.getAttribute("tags") ?? "",
      description: r.querySelector("description")?.textContent?.trim() ?? "",
      failedChecks: num(r, "failedChecks") || checks.length,
      pages: Array.from(pages).sort((a, b) => a - b),
      checks: checks.slice(0, MAX_CHECKS).map((c) => ({
        context: c.querySelector("context")?.textContent ?? "",
        message: c.querySelector("errorMessage")?.textContent?.trim() ?? "",
      })),
    };
  });
  rules.sort((a, b) => b.failedChecks - a.failedChecks);
  return {
    profile: vr.getAttribute("profileName") ?? "veraPDF",
    compliant: vr.getAttribute("isCompliant") === "true",
    version: doc.querySelector("releaseDetails[id='core']")?.getAttribute("version") ?? "",
    passedRules: num(details, "passedRules"),
    failedRules: num(details, "failedRules"),
    passedChecks: num(details, "passedChecks"),
    failedChecks: num(details, "failedChecks"),
    rules,
  };
}

/** "1–3, 7, 9–12": compact page ranges. */
function pageRanges(pages: number[]): string {
  if (pages.length === 0) return "–";
  const out: string[] = [];
  let start = pages[0];
  let prev = pages[0];
  for (const p of [...pages.slice(1), Infinity]) {
    if (p === prev + 1) {
      prev = p;
      continue;
    }
    out.push(start === prev ? `${start}` : `${start}–${prev}`);
    start = prev = p;
  }
  return out.length > 12 ? `${out.slice(0, 12).join(", ")} and ${out.length - 12} more` : out.join(", ");
}

export function VeraPdfView({ report, onDownload, downloadLabel = "Download XML" }: {
  report: VeraReport; onDownload?: () => void; downloadLabel?: string;
}) {
  const [filter, setFilter] = useState("");
  const rules = report.rules.filter(
    (r) => !filter || `${r.clause} ${r.tags} ${r.description}`.toLowerCase().includes(filter.toLowerCase()),
  );
  return (
    <div className="panel" style={{ border: 0, padding: 0 }}>
      <div className="actions">
        {report.compliant ? (
          <span className="pill good">Compliant</span>
        ) : (
          <span className="pill bad">Not compliant</span>
        )}
        <span>{report.profile}</span>
        {report.version && <span className="muted small">veraPDF {report.version}</span>}
        {onDownload && <button className="btn secondary small" onClick={onDownload}>{downloadLabel}</button>}
      </div>
      <dl className="grid" style={{ margin: 0, gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
        <div className="stat"><dt className="lbl">Rules passed</dt><dd className="num" style={{ margin: 0 }}>{fmtNum(report.passedRules)}</dd></div>
        <div className="stat"><dt className="lbl">Rules failed</dt><dd className="num" style={{ margin: 0 }}>{fmtNum(report.failedRules)}</dd></div>
        <div className="stat"><dt className="lbl">Checks passed</dt><dd className="num" style={{ margin: 0 }}>{fmtNum(report.passedChecks)}</dd></div>
        <div className="stat"><dt className="lbl">Checks failed</dt><dd className="num" style={{ margin: 0 }}>{fmtNum(report.failedChecks)}</dd></div>
      </dl>
      {report.rules.length === 0 ? (
        <p className="empty">No failed rules.</p>
      ) : (
        <>
          {report.rules.length > 6 && (
            <div className="field" style={{ maxWidth: 360 }}>
              <label htmlFor="vera-filter">Filter failed rules</label>
              <input id="vera-filter" type="search" placeholder="clause, category or text" value={filter}
                onChange={(e) => setFilter(e.target.value)} />
            </div>
          )}
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Failed rules, most failed checks first</caption>
              <thead>
                <tr>
                  <th scope="col">Clause</th>
                  <th scope="col">Requirement</th>
                  <th scope="col" className="num">Failed checks</th>
                  <th scope="col">Pages</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={`${r.clause}-${r.test}`}>
                    <td className="mono" style={{ whiteSpace: "nowrap" }}>
                      {r.clause}-{r.test}
                      {r.tags && <div className="muted small">{r.tags.replace(/,/g, ", ")}</div>}
                    </td>
                    <td>
                      {r.description}
                      {r.checks.length > 0 && (
                        <details>
                          <summary className="small">Examples</summary>
                          <ul className="small">
                            {r.checks.map((c, i) => (
                              <li key={i}>
                                {c.message}
                                <div className="mono muted" style={{ overflowWrap: "anywhere" }}>{c.context}</div>
                              </li>
                            ))}
                          </ul>
                          {r.failedChecks > r.checks.length && (
                            <p className="muted small">First {r.checks.length} of {fmtNum(r.failedChecks)}. All are in the XML.</p>
                          )}
                        </details>
                      )}
                    </td>
                    <td className="num">{fmtNum(r.failedChecks)}</td>
                    <td className="small">{pageRanges(r.pages)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
