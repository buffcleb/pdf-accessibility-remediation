import { Link } from "react-router-dom";
import type { Job } from "../api";
import type { BackState } from "../pages/JobDetail";
import { HelpStatusPill } from "./help";
import { JobStatus, Verdict, fmtDate, fmtNum } from "./ui";

/** How a document was processed, in a few words: the route when it isn't the standard one, audit only, and the
 * AI level by name. */
export function howProcessed(j: Job): string {
  const parts = [j.processing === "adobe" ? "Adobe Autotag" : j.processing === "standard" ? "" : j.processing,
    j.report_only ? "Audit only" : "", j.ai_label ?? ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Standard";
}

/** Rows that can be selected, and what's selected (by id). */
export type Selection = { can: (j: Job) => boolean; selected: Set<string>; onChange: (next: Set<string>) => void };

/** Documents as a table. With `onSort`, the Document, Submitted, Pages and Result headings sort (and User, where
 * it's shown); with `back`, a document's page links back to this exact view; with `selection`, rows have
 * checkboxes; `showService` adds how each was processed, for admins looking across people. */
export function JobsTable({ jobs, showUser = false, showService = false, sort, onSort, back, selection }: {
  jobs: Job[]; showUser?: boolean; showService?: boolean; sort?: string; onSort?: (key: string) => void; back?: BackState;
  selection?: Selection;
}) {
  if (jobs.length === 0) return <p className="empty">No documents yet.</p>;
  const pickable = selection ? jobs.filter(selection.can) : [];
  const allOn = pickable.length > 0 && pickable.every((j) => selection!.selected.has(j.id));
  const toggle = (ids: string[], on: boolean) => {
    const next = new Set(selection!.selected);
    for (const id of ids) { if (on) next.add(id); else next.delete(id); }
    selection!.onChange(next);
  };
  const head = (key: string, label: string, className?: string) => {
    if (!onSort) return <th scope="col" className={className}>{label}</th>;
    const on = sort === key || sort === `-${key}`;
    const desc = sort === `-${key}`;
    return (
      <th scope="col" className={className} aria-sort={on ? (desc ? "descending" : "ascending") : "none"}>
        <button type="button" className="sort-btn" onClick={() => onSort(key)}>
          {label}<span aria-hidden="true">{on ? (desc ? " ▼" : " ▲") : ""}</span>
        </button>
      </th>
    );
  };
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {selection && <th scope="col" className="select-col">
              <input type="checkbox" aria-label="Select all the documents shown that can be selected" checked={allOn}
                disabled={pickable.length === 0} onChange={(e) => toggle(pickable.map((j) => j.id), e.target.checked)} /></th>}
            {head("filename", "Document")}
            {showUser && (onSort ? head("user", "User") : <th scope="col">User</th>)}
            {head("created_at", "Submitted")}
            {head("pages", "Pages", "num")}
            {showService && <th scope="col">Processing and AI</th>}
            <th scope="col">Status</th>
            {head("result", "Result")}
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              {selection && <td className="select-col">
                {selection.can(j) && <input type="checkbox" aria-label={`Select ${j.filename}`} checked={selection.selected.has(j.id)}
                  onChange={(e) => toggle([j.id], e.target.checked)} />}</td>}
              <td><Link to={`/jobs/${j.id}`} state={back}>{j.filename}</Link>
                {j.help && <> <HelpStatusPill status={j.help.status} forPerson />{j.help.unread && <span className="dot" title="New reply"><span className="visually-hidden"> (new reply)</span></span>}</>}
                {j.files_deleted ? <div className="muted small">Files removed</div>
                  : j.removal_at ? <div className="muted small">Files go {fmtDate(j.removal_at)}</div> : null}</td>
              {showUser && <td>{j.user_email}</td>}
              <td>{fmtDate(j.created_at)}</td>
              <td className="num">{fmtNum(j.pages)}</td>
              {showService && <td className="small">{howProcessed(j)}</td>}
              <td><JobStatus job={j} /></td>
              <td><Verdict job={j} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
