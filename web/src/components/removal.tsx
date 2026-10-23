import { useState, type ReactNode } from "react";
import { post, type Job, type RemovalResult } from "../api";
import { ErrorNotice, fmtNum } from "./ui";

const FINISHED = ["completed", "failed", "canceled"];

/** Documents whose files can be marked for early removal by their owner. */
export const canMarkDone = (j: Job) => FINISHED.includes(j.status) && !j.files_deleted && !j.removal_requested
  && !(j.help && ["open", "in_progress", "waiting"].includes(j.help.status));

/** Documents a super admin can remove files from or delete. */
export const canAdminRemove = (j: Job) => FINISHED.includes(j.status) && !j.deleted;

function outcome(r: RemovalResult, did: string): string {
  const parts = [`${fmtNum(r.done.length)} ${r.done.length === 1 ? "document" : "documents"} ${did}.`];
  if (r.skipped.length) {
    const why = [...new Set(r.skipped.map((s) => s.message))].join(" ");
    parts.push(`${fmtNum(r.skipped.length)} skipped: ${why}`);
  }
  return parts.join(" ");
}

/** My documents: mark the selected documents done (their files go after the hold). */
export function MarkDoneBar({ selected, holdHours, onDone, extra }: {
  selected: Set<string>; holdHours: number; onDone: (message: string) => void; extra?: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (selected.size === 0) return null;
  const n = selected.size;
  const when = holdHours === 0 ? "at the next cleanup (within the hour)" : `in ${holdHours} ${holdHours === 1 ? "hour" : "hours"}`;

  async function go() {
    if (!window.confirm(`Remove the files of ${fmtNum(n)} ${n === 1 ? "document" : "documents"} ${when}?\n\n`
      + "Download anything you need first. You can undo this on each document's page until then. The documents stay in "
      + "your list, without their files.")) return;
    setBusy(true);
    setError(null);
    try {
      const r = await post<RemovalResult>("/jobs/remove-early", { ids: [...selected] });
      onDone(outcome(r, `marked done; their files go ${when}`));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="selection-bar" role="region" aria-label="Selected documents">
      <span className="small">{fmtNum(n)} selected</span>
      {extra}
      <button type="button" className="btn small" disabled={busy} onClick={() => void go()}>I'm done with these: remove the files</button>
      <button type="button" className="linkbtn small" onClick={() => onDone("")}>Clear selection</button>
      <ErrorNotice error={error} />
    </div>
  );
}

/** Super admins: remove the selected documents' files now, or delete the documents. */
export function AdminRemoveBar({ selected, onDone, extra }: { selected: Set<string>; onDone: (message: string) => void;
  extra?: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  if (selected.size === 0) return null;
  const n = selected.size;
  const docs = `${fmtNum(n)} ${n === 1 ? "document" : "documents"}`;

  async function go(del: boolean) {
    const what = del
      ? `Delete ${docs}?\n\nTheir files are removed now and they leave every document list, including their owners'. `
        + "Reports and page usage still count them. This can't be undone."
      : `Remove the files of ${docs} now?\n\nThe documents stay in their owners' lists, without their files. This can't be undone.`;
    if (!window.confirm(what)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await post<RemovalResult>("/admin/jobs/remove", { ids: [...selected], delete: del, reason: reason.trim() });
      setReason("");
      onDone(outcome(r, del ? "deleted" : "had their files removed"));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const ok = reason.trim().length >= 3 && !busy;
  return (
    <div className="selection-bar" role="region" aria-label="Selected documents">
      <span className="small">{docs} selected</span>
      {extra}
      <label className="small" htmlFor="rm-reason">Reason</label>
      <input id="rm-reason" type="text" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="Recorded in the audit log" aria-describedby="rm-reason-hint" />
      <span id="rm-reason-hint" className="visually-hidden">At least 3 characters.</span>
      <button type="button" className="btn secondary small" disabled={!ok} onClick={() => void go(false)}>Remove files now</button>
      <button type="button" className="btn secondary small danger-text" disabled={!ok} onClick={() => void go(true)}>Delete</button>
      <button type="button" className="linkbtn small" onClick={() => onDone("")}>Clear selection</button>
      <ErrorNotice error={error} />
    </div>
  );
}

/** Selected documents with nothing to do to them but download them (group admins). */
export function SelectedBar({ selected, onDone, children }: { selected: Set<string>; onDone: (message: string) => void;
  children: ReactNode }) {
  if (selected.size === 0) return null;
  return (
    <div className="selection-bar" role="region" aria-label="Selected documents">
      <span className="small">{fmtNum(selected.size)} selected</span>
      {children}
      <button type="button" className="linkbtn small" onClick={() => onDone("")}>Clear selection</button>
    </div>
  );
}
