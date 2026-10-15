import { Fragment, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { get, post } from "../../api";
import { ErrorNotice, Loading, fmtDate, fmtNum, useLoad, useTitle } from "../../components/ui";
import { QueuesLive } from "./QueuesLive";

type Queue = {
  key: string; label: string; kind: "documents" | "edits" | "reviews" | "mail";
  waiting: number; active: number; failed: number; sent: number | null; oldest_waiting: string | null;
  paused: { by: string; at: string; note: string | null } | null;
};
type Item = {
  id: string; job_id: string | null; title: string; who: string; pages: number | null; options: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; detail: string | null;
  error_code: string | null; can_retry: boolean; can_cancel: boolean; can_stop?: boolean; can_remove?: boolean; can_process_now: boolean;
};
type State = "waiting" | "active" | "failed";

const always = () => true;
const path = (key: string) => `/admin/queues/${encodeURIComponent(key)}`;

/** How long ago, short: "45 min", "3 h", "2 d". */
function age(since: string) {
  const mins = Math.max(0, (Date.now() - new Date(since).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)} min`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h`;
  return `${Math.round(mins / 1440)} d`;
}

const ago = (since: string) => { const a = age(since); return a === "just now" ? a : `${a} ago`; };

/** Super admins: every queue, how much is in it, and pause or resume it; then the items of one queue. */
export function QueuesPage() {
  useTitle("Queues");
  const [params, setParams] = useSearchParams();
  const queues = useLoad(() => get<Queue[]>("/admin/queues"), [], always, 10000);
  const [error, setError] = useState<unknown>(null);
  const [pausing, setPausing] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  if (queues.loading && !queues.data) return <Loading />;
  if (!queues.data) return <ErrorNotice error={queues.error} />;
  const selected = queues.data.find((q) => q.key === params.get("queue")) ?? null;
  const liveView = params.get("view") === "live";
  const views = (
    <nav className="report-pills" aria-label="Queues: which view">
      <Link className="pill-link" to="/admin/queues" aria-current={liveView ? undefined : "page"}>By queue</Link>
      <Link className="pill-link" to="?view=live" aria-current={liveView ? "page" : undefined}>Live</Link>
    </nav>
  );
  if (liveView) {
    return (
      <>
        <h1>Queues</h1>
        <p className="muted">Everything the queues are doing now, in one list: what is in progress and how far it has got, what
          is waiting and why, and what has just ended.</p>
        {views}
        <QueuesLive queues={queues.data} />
      </>
    );
  }

  async function act(fn: () => Promise<Queue[]>) {
    setBusy(true);
    setError(null);
    try {
      queues.setData(await fn());
      setPausing(null);
      setNote("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Queues</h1>
      <p className="muted">Everything that waits its turn. <strong>Pause</strong> lets work already started finish and starts
        nothing new until you resume it; documents for a paused service wait in its queue and go ahead on resume.
        Sign-in codes are always sent at once and can't be paused. <strong>Failed</strong> counts the last 7 days;
        <strong> Oldest</strong> is how long the oldest item has been waiting.</p>
      {views}
      <ErrorNotice error={error} />
      <div className="table-wrap">
        <table className="queues">
          <thead><tr><th scope="col">Queue</th><th scope="col" className="num">Waiting</th>
            <th scope="col" className="num">In progress</th><th scope="col" className="num">Failed</th>
            <th scope="col" className="num">Oldest</th><th scope="col">State</th>
            <th scope="col"><span className="visually-hidden">Pause or resume</span></th></tr></thead>
          <tbody>
            {queues.data.map((q) => (
              <Fragment key={q.key}>
                <tr className={selected?.key === q.key || pausing === q.key ? "row-open" : undefined}>
                  <td><Link to={`?queue=${encodeURIComponent(q.key)}`} aria-current={selected?.key === q.key ? "true" : undefined}>
                    {q.label}</Link>
                    {q.kind === "mail" && q.sent !== null && <div className="muted small">{fmtNum(q.sent)} sent in 7 days</div>}</td>
                  <td className="num">{fmtNum(q.waiting)}</td>
                  <td className="num">{q.kind === "mail" ? "–" : fmtNum(q.active)}</td>
                  <td className="num">{q.failed ? <span className="pill bad">{fmtNum(q.failed)}</span> : "0"}</td>
                  <td className="num">{q.oldest_waiting ? age(q.oldest_waiting) : "–"}</td>
                  <td>{q.paused
                    ? <><span className="pill warn">Paused</span>
                      <div className="muted small">by {q.paused.by}, {ago(q.paused.at)}{q.paused.note ? `: ${q.paused.note}` : ""}</div></>
                    : <span className="pill good">Running</span>}</td>
                  <td>{q.paused
                    ? <button className="btn small" disabled={busy}
                      onClick={() => void act(() => post<Queue[]>(`${path(q.key)}/resume`))}>
                      Resume<span className="visually-hidden"> {q.label}</span></button>
                    : <button className="btn secondary small" aria-expanded={pausing === q.key}
                      onClick={() => { setPausing(pausing === q.key ? null : q.key); setNote(""); }}>
                      Pause<span className="visually-hidden"> {q.label}</span></button>}</td>
                </tr>
                {pausing === q.key && (
                  <tr className="row-panel"><td colSpan={7}>
                    <form className="form-row" onSubmit={(e) => { e.preventDefault(); void act(() => post<Queue[]>(`${path(q.key)}/pause`, { note: note || null })); }}>
                      <div className="field" style={{ flex: "1 1 280px" }}>
                        <label htmlFor="pause-note">Why <span className="hint">(optional, shown here and in the audit log)</span></label>
                        <input id="pause-note" type="text" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
                      </div>
                      <button className="btn small" type="submit" disabled={busy}>Pause {q.label}</button>
                      <button className="btn secondary small" type="button" onClick={() => setPausing(null)}>Keep running</button>
                    </form>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {selected
        ? <QueueItems key={selected.key} queue={selected} onChange={() => void queues.reload()}
          onClose={() => setParams({}, { replace: true })} />
        : <p className="muted small">Choose a queue to see what's in it.</p>}
    </>
  );
}

function QueueItems({ queue, onChange, onClose }: { queue: Queue; onChange: () => void; onClose: () => void }) {
  const [params] = useSearchParams();
  const location = useLocation();
  const state = (["waiting", "active", "failed"].includes(params.get("state") ?? "") ? params.get("state") : "waiting") as State;
  const list = useLoad(() => get<{ items: Item[] }>(`${path(queue.key)}/items?state=${state}`), [queue.key, state], always, 10000);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState("");
  const isMail = queue.kind === "mail";
  const states: [State, string, number][] = [
    ["waiting", "Waiting", queue.waiting], ...(isMail ? [] : [["active", "In progress", queue.active] as [State, string, number]]),
    ["failed", "Failed, last 7 days", queue.failed]];
  const noun = isMail ? "email" : queue.kind === "documents" ? "document" : queue.kind === "edits" ? "save" : "review";

  async function act(item: Item, what: "retry" | "cancel" | "process-now" | "stop" | "remove") {
    const verb = { retry: "Try again", cancel: "Canceled", "process-now": "Process now", stop: "Stopped", remove: "Removed from the list" }[what];
    if (!window.confirm(what === "process-now"
      ? `Process "${item.title}" next? It goes ahead of the large-document rules as soon as a slot is free.`
      : what === "stop"
      ? `Stop "${item.title}"? It ends now and frees its place.${queue.kind === "documents" ? " The person's pages are given back and they're emailed; they can send it again." : queue.kind === "edits" ? " The save is lost; the previous version is unchanged." : ""}`
      : what === "remove"
      ? `Remove "${item.title}" from this list? ${isMail ? "The email" : "The " + noun} itself isn't changed${queue.kind === "documents" ? ": it stays in the person's documents as failed" : ""}; it just stops being listed here.`
      : what === "cancel"
      ? `Cancel "${item.title}"?${queue.kind === "documents" ? " The person's pages are given back and they're emailed." : ""}`
      : `Try "${item.title}" again?${queue.kind === "documents" ? " Its pages are held again on the person's own limits." : ""}`)) return;
    setError(null);
    setDone("");
    try {
      await post(`${path(queue.key)}/items/${item.id}/${what}`);
      setDone(`${verb}: ${item.title}`);
      await list.reload();
      onChange();
    } catch (e) {
      setError(e);
    }
  }

  const when = state === "waiting" ? "Waiting since" : state === "active" ? "Started" : "Failed";
  return (
    <section className="queue-items" aria-labelledby="qi-title">
      <div className="section-head">
        <h2 id="qi-title">{queue.label}</h2>
        <button type="button" className="linkbtn small" onClick={onClose}>Close</button>
      </div>
      <nav className="report-pills" aria-label={`${queue.label}: what to show`}>
        {states.map(([k, label, n]) => (
          <Link key={k} className="pill-link" to={`?queue=${encodeURIComponent(queue.key)}&state=${k}`} replace
            aria-current={k === state ? "page" : undefined}>{label} ({fmtNum(n)})</Link>
        ))}
      </nav>
      <ErrorNotice error={error} />
      <div role="status" className="small">{done}</div>
      {list.loading && !list.data ? <Loading /> : !list.data ? <ErrorNotice error={list.error} /> :
        list.data.items.length === 0 ? <p className="empty">Nothing {state === "waiting" ? "waiting" : state === "active" ? "in progress" : "failed in the last 7 days"}.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">{isMail ? "Subject" : "Item"}</th><th scope="col">{isMail ? "To" : "Person"}</th>
                {!isMail && <th scope="col" className="num">Pages</th>}
                <th scope="col">{when}</th><th scope="col">Details</th>
                <th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
              <tbody>
                {list.data.items.map((it) => (
                  <tr key={it.id}>
                    <td>{it.job_id ? <Link to={`/jobs/${it.job_id}`} state={{ back: location.pathname + location.search, backLabel: "Queues" }}>{it.title}</Link> : it.title}
                      {it.options && <div className="muted small">{it.options}</div>}</td>
                    <td>{it.who}</td>
                    {!isMail && <td className="num">{it.pages === null ? "–" : fmtNum(it.pages)}</td>}
                    <td className="small">{fmtDate(state === "waiting" ? it.created_at : state === "active" ? it.started_at : it.finished_at)}</td>
                    <td className="small">{it.detail ?? <span className="muted">–</span>}
                      {it.error_code && <div className="muted">Code: {it.error_code}</div>}</td>
                    <td><div className="actions">
                      {it.can_process_now && <button className="btn small" onClick={() => void act(it, "process-now")}>
                        Process now<span className="visually-hidden"> {it.title}</span></button>}
                      {it.can_retry && <button className="btn secondary small" onClick={() => void act(it, "retry")}>
                        Try again<span className="visually-hidden"> {it.title}</span></button>}
                      {it.can_cancel && <button className="btn secondary small" onClick={() => void act(it, "cancel")}>
                        Cancel<span className="visually-hidden"> {noun} {it.title}</span></button>}
                      {it.can_stop && <button className="btn secondary small" onClick={() => void act(it, "stop")}>
                        Stop<span className="visually-hidden"> {noun} {it.title}</span></button>}
                      {it.can_remove && <button className="btn secondary small" onClick={() => void act(it, "remove")}>
                        Remove<span className="visually-hidden"> {noun} {it.title} from the list</span></button>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {list.data && list.data.items.length >= 200 && <p className="muted small">Showing the first 200.</p>}
    </section>
  );
}
