import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { get, post, type ReportView } from "../api";
import { useAuth } from "../auth";
import { ErrorNotice, Loading, Notice, useLoad } from "./ui";

type Person = { id: string; email: string };
export const VIEW_FREQUENCIES: [ReportView["frequency"], string][] = [["monthly", "Monthly"], ["weekly", "Weekly"], ["daily", "Daily"]];
const NOT_SAVED = new Set(["report", "range", "since", "until", "page", "page_size", "group_id"]);

/** "Email this view": save what's on screen (filters, search, sort, columns, group) to be emailed each period to
 * you and others with the same access. The date range is replaced by the period each time. */
export function ScheduleView({ report, label, params, groupId, onClose }: {
  report: string; label: string; params: URLSearchParams; groupId: string | null; onClose: () => void;
}) {
  const { me } = useAuth();
  const people = useLoad(() => get<Person[]>(`/me/report-views/recipients?report=${report}${groupId ? `&group_id=${groupId}` : ""}`),
    [report, groupId]);
  const [name, setName] = useState(label);
  const [frequency, setFrequency] = useState<ReportView["frequency"]>("monthly");
  const [to, setTo] = useState<Set<string>>(new Set(me ? [me.id] : []));
  const [csv, setCsv] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<ReportView | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const kept = Object.fromEntries([...params].filter(([k, v]) => !NOT_SAVED.has(k) && v));
      setDone(await post<ReportView>("/me/report-views", {
        name, report, params: kept, group_id: groupId, frequency, recipients: [...to], attach_csv: csv,
      }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="report-more schedule-view" role="region" aria-label="Email this view">
        <Notice kind="good" title="Scheduled">
          &ldquo;{done.name}&rdquo; will be emailed {done.frequency} to {done.recipients.map((p) => p.email).join(", ")}.
          Change or stop it in <Link to="/settings">Settings</Link>.
        </Notice>
        <button type="button" className="linkbtn small" onClick={onClose}>Close</button>
      </div>
    );
  }
  return (
    <form className="report-more schedule-view" onSubmit={(e) => void save(e)} aria-label="Email this view">
      <p className="small muted" style={{ margin: 0 }}>Emails this report as you see it (filters, search, sort and
        columns) for the period just ended: last month, last week or yesterday. Only people who can see it in the app can get it.</p>
      <div className="field" style={{ flex: "1 1 240px" }}>
        <label htmlFor="sv-name">Name</label>
        <input id="sv-name" type="text" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <fieldset className="field">
        <legend>How often</legend>
        <div className="segmented">
          {VIEW_FREQUENCIES.map(([f, l]) => (
            <label key={f} className={frequency === f ? "on" : undefined}>
              <input type="radio" name="sv-freq" value={f} checked={frequency === f} onChange={() => setFrequency(f)} />{l}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="field sv-people">
        <legend>Send to</legend>
        {people.loading && !people.data ? <Loading /> : !people.data ? <ErrorNotice error={people.error} /> : (
          <div className="choices">
            {people.data.map((p) => (
              <label className="check" key={p.id}>
                <input type="checkbox" checked={to.has(p.id)} onChange={(e) => {
                  const next = new Set(to);
                  if (e.target.checked) next.add(p.id); else next.delete(p.id);
                  setTo(next);
                }} /><span>{p.email}{p.id === me?.id ? " (you)" : ""}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>
      <label className="check"><input type="checkbox" checked={csv} onChange={(e) => setCsv(e.target.checked)} />
        <span>Attach every row as a CSV</span></label>
      <ErrorNotice error={error} />
      <div className="actions">
        <button className="btn small" type="submit" disabled={busy || to.size === 0 || !name.trim()}>Schedule</button>
        <button className="btn secondary small" type="button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}
