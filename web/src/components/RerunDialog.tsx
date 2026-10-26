import { useEffect, useRef, useState } from "react";
import { PDFUA_LABELS, type Job, type PdfUa } from "../api";
import { fmtNum } from "./ui";

/** A super admin sends a document through again (#323): a new document for the same owner with the same file and
 * options. Its pages are charged again only when asked to; the reason goes to the audit log. */
export function RerunDialog({ job, busy, onRun, onClose }: {
  job: Job; busy: boolean; onRun: (charge: boolean, reason: string, ignoreExisting: boolean, pdfua: PdfUa) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [charge, setCharge] = useState(false);
  const [ignoreTags, setIgnoreTags] = useState(job.ignore_existing);
  const [pdfua, setPdfua] = useState<PdfUa>(job.pdfua ?? "1");
  // The standard can be changed when it was PDF/UA-2 or Both: the owner's group allowed that (#445)
  const [reason, setReason] = useState("");
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  const chargeable = !job.report_only;
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="rerun-h" aria-describedby="rerun-d"
      onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <h2 id="rerun-h">Re-run this document?</h2>
      <p id="rerun-d">
        A new document is made for its owner with the same file and options, and goes into the queue. This one, its
        versions and edits stay as they are; each links to the other.
      </p>
      {chargeable && (
        <label className="check">
          <input type="checkbox" checked={charge} onChange={(e) => setCharge(e.target.checked)} />
          <span>Charge the owner's pages again ({fmtNum(job.pages)} pages)</span>
        </label>
      )}
      {!job.report_only && (
        <label className="check">
          <input type="checkbox" checked={ignoreTags} onChange={(e) => setIgnoreTags(e.target.checked)} />
          <span>Ignore the document's existing tags <span className="muted">(tag it afresh from the page, for a PDF
            tagged badly)</span></span>
        </label>
      )}
      {!job.report_only && (job.pdfua ?? "1") !== "1" && (
        <div>
          <label htmlFor="rerun-pdfua">Accessibility standard</label>
          <select id="rerun-pdfua" value={pdfua} onChange={(e) => setPdfua(e.target.value as PdfUa)}>
            {(["1", "2", "both"] as PdfUa[]).map((p) => <option key={p} value={p}>{PDFUA_LABELS[p]}</option>)}
          </select>
        </div>
      )}
      <p className="muted small">{charge && chargeable
        ? "The pages are held on the owner's limits, as for an upload, and refused if they don't have enough."
        : "Not charged: it's support's own re-run."}</p>
      <div>
        <label htmlFor="rerun-reason">Reason (optional, kept in the audit log)</label>
        <input id="rerun-reason" type="text" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <div className="actions">
        <button className="btn" type="button" disabled={busy} onClick={() => onRun(charge && chargeable, reason.trim(), ignoreTags && !job.report_only, pdfua)}>
          {busy ? "Re-running…" : "Re-run"}</button>
        <button className="btn secondary" type="button" autoFocus onClick={onClose}>Cancel</button>
      </div>
    </dialog>
  );
}
