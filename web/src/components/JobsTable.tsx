import { Link } from "react-router-dom";
import type { Job } from "../api";
import type { BackState } from "../pages/JobDetail";
import { HelpStatusPill } from "./help";
import { JobStatus, Verdict, fmtDate, fmtNum } from "./ui";

/** Documents as a table. With `onSort`, the Document, Submitted, Pages and Result headings sort; with `back`, a
 * document's page links back to this exact view. */
export function JobsTable({ jobs, showUser = false, sort, onSort, back }: {
  jobs: Job[]; showUser?: boolean; sort?: string; onSort?: (key: string) => void; back?: BackState;
}) {
  if (jobs.length === 0) return <p className="empty">No documents yet.</p>;
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
            {head("filename", "Document")}
            {showUser && <th scope="col">User</th>}
            {head("created_at", "Submitted")}
            {head("pages", "Pages", "num")}
            <th scope="col">Status</th>
            {head("result", "Result")}
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td><Link to={`/jobs/${j.id}`} state={back}>{j.filename}</Link>
                {j.help && <> <HelpStatusPill status={j.help.status} forPerson />{j.help.unread && <span className="dot" title="New reply"><span className="visually-hidden"> (new reply)</span></span>}</>}</td>
              {showUser && <td>{j.user_email}</td>}
              <td>{fmtDate(j.created_at)}</td>
              <td className="num">{fmtNum(j.pages)}</td>
              <td><JobStatus job={j} /></td>
              <td><Verdict job={j} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
