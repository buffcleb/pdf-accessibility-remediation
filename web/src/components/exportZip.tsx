import { useState } from "react";
import { downloadPost, post } from "../api";
import { ErrorNotice, fmtBytes, fmtNum } from "./ui";

/** The documents to export: the ones chosen, or everything the list shows (with its filters, every page). */
export type ExportRequest = { ids: string[] } | { filters: Record<string, string | boolean> };

type ExportPlan = { matched: number; limit: number; documents: number; files: number; size_bytes: number;
  left_out: { filename: string; reason: string }[] };

const docs = (n: number) => `${fmtNum(n)} ${n === 1 ? "document" : "documents"}`;

/** Download documents' files as one zip (#481): first what it will hold (how many documents and files, how big, what's
 * left out and why), then the zip, made on the fly, with how much has arrived. `endpoint` is the list's
 * (/documents, or a group's). */
export function ExportZip({ endpoint, request, label }: { endpoint: string; request: ExportRequest; label: string }) {
  const [plan, setPlan] = useState<ExportPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [received, setReceived] = useState<number | null>(null);
  const [done, setDone] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function check() {
    setError(null);
    setDone("");
    setBusy(true);
    try {
      setPlan(await post<ExportPlan>(`${endpoint}/export/plan`, request));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function fetchZip() {
    if (!plan) return;
    setError(null);
    setBusy(true);
    setReceived(0);
    let last = 0;
    try {
      await downloadPost(`${endpoint}/export`, request, "documents.zip", (n) => {
        // A screen reader hears the status line change: a few times a second at most
        if (n - last > 1_000_000 || n < last) { last = n; setReceived(n); }
      });
      setDone(`Downloaded ${docs(plan.documents)} as a zip.`);
      setPlan(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      setReceived(null);
    }
  }

  const over = !!plan && plan.matched > plan.limit;
  return (
    <>
      <button type="button" className="btn secondary small" disabled={busy} aria-expanded={!!plan} onClick={() => void check()}>{label}</button>
      {(plan || done || error) && (
        <div className="export-zip" role="region" aria-label="Download as a zip">
          {done && <p className="small" role="status">{done}</p>}
          {plan && over && <p className="small">That's {docs(plan.matched)}; one zip holds at most {fmtNum(plan.limit)}. Choose
            fewer, or narrow the list with search and filters.</p>}
          {plan && !over && plan.documents === 0 && <p className="small">None of these has files to download.</p>}
          {plan && !over && plan.documents > 0 && <>
            <p className="small">{docs(plan.documents)}, {fmtNum(plan.files)} files, {fmtBytes(plan.size_bytes)}: one folder per
              document, named from its date and file name.</p>
            {plan.left_out.length > 0 && <>
              <p className="small">Left out, and listed in README.txt in the zip:</p>
              <ul className="small export-left-out">
                {plan.left_out.map((l, i) => <li key={i}><strong>{l.filename}</strong>: {l.reason}</li>)}
              </ul>
            </>}
          </>}
          <p className="small export-actions">
            {plan && !over && plan.documents > 0 && <button type="button" className="btn small" disabled={busy}
              onClick={() => void fetchZip()}>Download zip</button>}
            {received !== null && <span role="status" aria-live="polite">Downloading… {fmtBytes(received)} of about {fmtBytes(plan?.size_bytes ?? 0)}</span>}
            {!busy && <button type="button" className="linkbtn small" onClick={() => { setPlan(null); setDone(""); setError(null); }}>
              {plan ? "Cancel" : "Close"}</button>}
          </p>
          <ErrorNotice error={error} />
        </div>
      )}
    </>
  );
}
