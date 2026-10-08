import { useState } from "react";
import { download, type LedgerEntry } from "../api";
import { ErrorNotice, fmtDate, fmtNum } from "./ui";

const KIND: Record<LedgerEntry["kind"], string> = {
  topup: "Top-up",
  adjust: "Adjustment",
  reserve: "Reserved",
  commit: "Used",
  release: "Returned",
  correct: "Correction",
};

export function LedgerTable({ entries, emails }: { entries: LedgerEntry[]; emails?: Record<string, string> }) {
  if (entries.length === 0) return <p className="empty">No entries yet.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Entry</th>
            <th scope="col" className="num">Pages</th>
            <th scope="col">Ticket / reason</th>
            <th scope="col">By</th>
            <th scope="col" className="num">Pool after</th>
            <th scope="col" className="num">Used after</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td>{fmtDate(e.created_at)}</td>
              <td>
                {KIND[e.kind]}
                {e.service_key && e.service_key !== "standard" && <> <span className="pill info">{e.service_key}</span></>}
                {e.scope === "global" && <> <span className="pill muted">Global pool</span></>}
                {e.override && <> <span className="pill warn">Override</span></>}
                {e.user_id && emails?.[e.user_id] ? <span className="muted small"> · {emails[e.user_id]}</span> : null}
              </td>
              <td className="num">{e.kind === "adjust" && e.pages > 0 ? "+" : ""}{fmtNum(e.pages)}</td>
              <td className="small">
                {e.ticket_url ? (
                  <a href={e.ticket_url} target="_blank" rel="noreferrer">{e.ticket_ref ?? "ticket"}</a>
                ) : (
                  e.ticket_ref && <span className="mono">{e.ticket_ref}</span>
                )}
                {e.comment && <div>{e.comment}</div>}
              </td>
              <td className="small">{e.actor_email}</td>
              <td className="num">{fmtNum(e.pool_after)}</td>
              <td className="num">{fmtNum(e.used_after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Date range plus one download button per CSV report. */
export function CsvExports({ base, reports }: { base: string; reports: { name: string; label: string; hint: string }[] }) {
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function get(name: string) {
    setError(null);
    setBusy(name);
    const q = new URLSearchParams();
    if (since) q.set("since", new Date(since + "T00:00:00").toISOString());
    if (until) q.set("until", new Date(until + "T00:00:00").toISOString());
    try {
      await download(`${base}/${name}.csv${q.toString() ? "?" + q : ""}`, `${name}.csv`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="panel">
      <div className="form-row">
        <div className="field">
          <label htmlFor="since">From <span className="hint">(optional)</span></label>
          <input id="since" type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="until">Up to, not including <span className="hint">(optional)</span></label>
          <input id="until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
      </div>
      <ul style={{ display: "flex", flexDirection: "column", gap: 12, paddingLeft: 0, listStyle: "none", margin: 0 }}>
        {reports.map((r) => (
          <li key={r.name} className="actions">
            <button className="btn secondary" disabled={busy !== null} onClick={() => void get(r.name)}>
              {busy === r.name ? "Preparing…" : `Download ${r.label} (CSV)`}
            </button>
            <span className="muted small">{r.hint}</span>
          </li>
        ))}
      </ul>
      <p className="muted small">Every export is recorded in the audit log.</p>
      <ErrorNotice error={error} />
    </div>
  );
}
