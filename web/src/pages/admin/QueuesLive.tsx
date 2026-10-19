import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { get, post, type Group } from "../../api";
import { ErrorNotice, Loading, fmtNum, useLoad } from "../../components/ui";
import { LIVE_STATES, howLong, liveQuery, stageLabel, type Live, type LiveItem } from "./liveRows";

const REFRESH_MS = 3000;
const PILL: Record<LiveItem["state"], string> = { active: "info", waiting: "note", done: "good", failed: "bad", canceled: "muted" };
const STATE_NAME = Object.fromEntries(LIVE_STATES) as Record<string, string>;

/** Every queue's items in one list as they move: what is in progress and the stage it has reached, what waits and
 * why, and what has just ended. Narrowed by search, group, queue and state, all kept in the address. It refreshes
 * every few seconds until it is paused, so a row can be read without it moving away. */
export function QueuesLive({ queues }: { queues: { key: string; label: string }[] }) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [paused, setPaused] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState("");
  const groups = useLoad(() => get<Group[]>("/admin/groups"), []);
  const query = liveQuery(params);
  const running = useCallback(() => !paused, [paused]);
  const live = useLoad(() => get<Live>(`/admin/queues/live${query ? `?${query}` : ""}`), [query], running, REFRESH_MS);

  // The times shown count up between refreshes
  useEffect(() => {
    if (paused) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [paused]);

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k); else next.set(k, v);
    }
    setParams(next, { replace: true });
  }

  // Search as you type, a moment after typing stops
  useEffect(() => {
    if ((params.get("q") ?? "") === search) return;
    const t = window.setTimeout(() => update({ q: search || null }), 350);
    return () => window.clearTimeout(t);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(item: LiveItem, what: "cancel" | "stop" | "restart" | "process-now") {
    const question = what === "process-now"
      ? `Process "${item.title}" next? It goes ahead of the large-document rules as soon as a slot is free.`
      : what === "restart"
        ? `Restart "${item.title}"? The engine stops its copy and it goes back to the queue, keeping its place. Nothing is charged again and nobody is emailed.`
      : what === "stop"
        ? `Stop "${item.title}"? It ends now and frees its place.${item.kind === "documents" ? " The person's pages are given back and they're emailed." : ""}`
        : `Cancel "${item.title}"?${item.kind === "documents" ? " The person's pages are given back and they're emailed." : ""}`;
    if (!window.confirm(question)) return;
    setError(null);
    setDone("");
    try {
      await post(`/admin/queues/${encodeURIComponent(item.key)}/items/${item.id}/${what}`);
      setDone(`${{ cancel: "Canceled", stop: "Stopped", restart: "Restarted", "process-now": "Process now" }[what]}: ${item.title}`);
      await live.reload();
    } catch (e) {
      setError(e);
    }
  }

  const data = live.data;
  const state = params.get("state") ?? "";
  const filtering = !!(params.get("q") || params.get("group") || params.get("in") || state);
  const back = { back: location.pathname + location.search, backLabel: "Queues: live" };
  return (
    <section className="queue-live" aria-labelledby="ql-h">
      <h2 id="ql-h" className="visually-hidden">Live</h2>
      <form className="report-bar" role="search" aria-label="Search and filter what the queues are doing" onSubmit={(e) => e.preventDefault()}>
        <div className="report-search-box">
          <label htmlFor="ql-q" className="visually-hidden">Search by document, person or email subject</label>
          <span className="search-icon" aria-hidden="true">⌕</span>
          <input id="ql-q" type="search" value={search} placeholder="Search by document, person or email subject"
            onChange={(e) => setSearch(e.target.value)} />
        </div>
        <label htmlFor="ql-group" className="visually-hidden">Group</label>
        <select id="ql-group" className="report-select" value={params.get("group") ?? ""} onChange={(e) => update({ group: e.target.value })}>
          <option value="">All groups</option>
          {(groups.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <label htmlFor="ql-queue" className="visually-hidden">Queue</label>
        <select id="ql-queue" className="report-select" value={params.get("in") ?? ""} onChange={(e) => update({ in: e.target.value })}>
          <option value="">All queues</option>
          {queues.map((q) => <option key={q.key} value={q.key}>{q.label}</option>)}
        </select>
        <button type="button" className={`btn small ${paused ? "" : "secondary"}`} aria-pressed={paused} onClick={() => setPaused(!paused)}>
          {paused ? "Resume updates" : "Pause updates"}</button>
      </form>
      <nav className="report-pills" aria-label="Which items to show">
        <button type="button" className="pill-link" aria-current={state === "" ? "true" : undefined} onClick={() => update({ state: null })}>
          All{data ? ` (${fmtNum(Object.values(data.counts).reduce((a, b) => a + b, 0))})` : ""}</button>
        {LIVE_STATES.map(([k, label]) => (
          <button key={k} type="button" className="pill-link" aria-current={state === k ? "true" : undefined} onClick={() => update({ state: k })}>
            {label}{data ? ` (${fmtNum(data.counts[k] ?? 0)})` : ""}</button>
        ))}
      </nav>
      <p className="small muted" role="status">
        {paused ? "Updates are paused: this is how things stood when you paused." : `Updates every ${REFRESH_MS / 1000} seconds.`}
        {data && ` Items that finished, failed or were canceled stay for ${data.recent_minutes} minutes.`}
        {data?.more && " There is more than this view takes: narrow it with the search or the filters."}
        {filtering && <> · <button type="button" className="linkbtn small" onClick={() => {
          setSearch("");
          setParams(new URLSearchParams({ view: "live" }), { replace: true });
        }}>Clear search and filters</button></>}
      </p>
      <ErrorNotice error={error ?? live.error} />
      <div role="status" className="small">{done}</div>
      {!data ? <Loading /> : data.items.length === 0
        ? <p className="empty">{filtering ? "Nothing matches." : "Nothing is waiting, in progress or just ended."}</p> : (
          <div className="table-wrap">
            <table className="queue-live-table">
              <thead><tr><th scope="col">Item</th><th scope="col">Person</th><th scope="col">Group</th><th scope="col">Queue</th>
                <th scope="col">Now</th><th scope="col" className="num">Pages</th>
                <th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
              <tbody>
                {data.items.map((it) => {
                  const stage = stageLabel(it);
                  return (
                    <tr key={`${it.kind}:${it.id}`}>
                      <td>{it.job_id ? <Link to={`/jobs/${it.job_id}`} state={back}>{it.title}</Link> : it.title}</td>
                      <td>{it.who}</td>
                      <td>{it.group_id ? <Link to={`/admin/groups/${it.group_id}`}>{it.group ?? "–"}</Link> : <span className="muted">–</span>}</td>
                      <td className="small">{it.queue}</td>
                      <td className="small">
                        <span className={`pill ${PILL[it.state]}`}>{STATE_NAME[it.state]}</span>{" "}
                        <span className="muted">{howLong(it, now)}</span>
                        {stage && <div>{stage}{it.percent !== null && <span className="muted"> · {it.percent}%</span>}</div>}
                        {it.state === "active" && it.percent !== null && (
                          <div className="meter" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={it.percent}
                            aria-label={`${it.title}: ${stage}`}><span style={{ width: `${it.percent}%` }} /></div>)}
                        {it.detail && <div className={it.state === "failed" ? "bad-text" : "muted"}>{it.detail}</div>}
                      </td>
                      <td className="num">{it.pages === null ? "–" : fmtNum(it.pages)}</td>
                      <td><div className="actions">
                        {it.can_process_now && <button className="btn small" onClick={() => void act(it, "process-now")}>
                          Process now<span className="visually-hidden"> {it.title}</span></button>}
                        {it.can_cancel && <button className="btn secondary small" onClick={() => void act(it, "cancel")}>
                          Cancel<span className="visually-hidden"> {it.title}</span></button>}
                        {it.can_restart && <button className="btn secondary small" onClick={() => void act(it, "restart")}>
                          Restart<span className="visually-hidden"> {it.title}</span></button>}
                        {it.can_stop && <button className="btn secondary small" onClick={() => void act(it, "stop")}>
                          Stop<span className="visually-hidden"> {it.title}</span></button>}
                      </div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
    </section>
  );
}
