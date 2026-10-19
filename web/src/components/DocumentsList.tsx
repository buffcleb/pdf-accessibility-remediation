import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { get, type Job } from "../api";
import { useAuth } from "../auth";
import { JobsTable, type Selection } from "./JobsTable";
import { MarkDoneBar, canMarkDone } from "./removal";
import { ErrorNotice, Loading, Notice, fmtNum, useLoad } from "./ui";

export type DocumentsPage = { items: Job[]; total: number; page: number; page_size: number; active: number };

const STATUSES: [string, string][] = [["", "All statuses"], ["held", "Waiting for you to confirm"], ["waiting", "Waiting"],
  ["in_progress", "In progress"], ["done", "Done"], ["failed", "Couldn't be processed"], ["canceled", "Canceled"]];
const RESULTS: [string, string][] = [["", "Any result"], ["passes", "Passes PDF/UA"], ["needs_work", "Needs work"],
  ["review", "Has items to review"], ["help", "Open help request"]];
const AUDIT: [string, string][] = [["", "Both"], ["false", "Remediated"], ["true", "Audit only"]];
const SIZES = [25, 50, 100, 200];

/** Another list of documents than the signed-in person's own: a group's, for its admins. */
export type DocumentsScope = {
  /** Where the page of documents comes from (answers like /documents) */
  endpoint: string;
  /** The services and AI levels offered in the filter */
  services: { key: string; label: string }[];
  /** What a document's page calls the way back to this list */
  backLabel: string;
  /** Which rows can be selected, and the bar that acts on the selection; without them rows have no checkboxes */
  can?: Selection["can"];
  bar?: (selected: Set<string>, onDone: (message: string) => void) => ReactNode;
};

/** Documents, a page at a time, with search, filters and sorting: the person's own, or with `scope` a group's for
 * its admins (each row then says whose it is and how it was processed, and the search also finds a person). The
 * whole view lives in the address, so Back and bookmarks work. Refreshes itself while any document is still being
 * processed. */
export function DocumentsList({ onStatusChange, scope }: { onStatusChange?: (key: string) => void; scope?: DocumentsScope }) {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [moreOpen, setMoreOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [doneMsg, setDoneMsg] = useState("");
  const size = Number(params.get("size")) || 25;
  const page = Number(params.get("page")) || 1;
  const sort = params.get("sort") ?? "-created_at";

  const api = new URLSearchParams();
  for (const k of ["q", "status", "result", "service", "audit"]) if (params.get(k)) api.set(k, params.get(k)!);
  if (params.get("from")) api.set("since", new Date(`${params.get("from")}T00:00:00`).toISOString());
  if (params.get("to")) api.set("until", new Date(new Date(`${params.get("to")}T00:00:00`).getTime() + 86400000).toISOString());
  api.set("sort", sort);
  api.set("page", String(page));
  api.set("page_size", String(size));
  const query = api.toString();
  const endpoint = scope?.endpoint ?? "/documents";
  const docs = useLoad(() => get<DocumentsPage>(`${endpoint}?${query}`), [endpoint, query],
    useCallback((d: DocumentsPage) => d.active > 0, []));

  // Tell the page when a document's status changes (to refresh pages left)
  const statusKey = (docs.data?.items ?? []).map((j) => `${j.id}:${j.status}`).join(",") + `|${docs.data?.active ?? ""}`;
  useEffect(() => { if (docs.data) onStatusChange?.(statusKey); }, [statusKey]); // eslint-disable-line react-hooks/exhaustive-deps

  function update(changes: Record<string, string | null>, keepPage = false) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k); else next.set(k, v);
    }
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  }

  // Search as you type, a moment after typing stops
  useEffect(() => {
    if ((params.get("q") ?? "") === search) return;
    const t = window.setTimeout(() => update({ q: search || null }), 350);
    return () => window.clearTimeout(t);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const services = scope?.services ?? (me?.services ?? []).filter((s) => s.available || s.used_pages > 0);
  const what = scope ? "the group's documents" : "your documents";
  const searchLabel = scope ? "Search by file name or person" : "Search by file name";
  const label = (list: [string, string][], v: string | null) => list.find(([k]) => k === v)?.[1] ?? v;
  const chips = [
    params.get("result") && { key: "result", text: label(RESULTS, params.get("result")) },
    params.get("service") && { key: "service", text: services.find((s) => s.key === params.get("service"))?.label ?? params.get("service") },
    params.get("audit") && { key: "audit", text: label(AUDIT, params.get("audit")) },
    params.get("from") && { key: "from", text: `From ${params.get("from")}` },
    params.get("to") && { key: "to", text: `To ${params.get("to")}` },
  ].filter(Boolean) as { key: string; text: string }[];
  const filtering = !!(params.get("q") || params.get("status") || chips.length);
  const data = docs.data;
  const done = (message: string) => {
    setSelected(new Set());
    setDoneMsg(message);
    if (message) void docs.reload();
  };
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const first = data && data.total ? (page - 1) * data.page_size + 1 : 0;
  const last = data ? Math.min(data.total, page * data.page_size) : 0;

  return (
    <div className="documents-list">
      <form className="report-bar" role="search" aria-label={`Search and filter ${what}`} onSubmit={(e) => e.preventDefault()}>
        <div className="report-search-box">
          <label htmlFor="dl-q" className="visually-hidden">{searchLabel}</label>
          <span className="search-icon" aria-hidden="true">⌕</span>
          <input id="dl-q" type="search" value={search} placeholder={searchLabel} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <label htmlFor="dl-status" className="visually-hidden">Status</label>
        <select id="dl-status" className="report-select" value={params.get("status") ?? ""} onChange={(e) => update({ status: e.target.value })}>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button type="button" className={`btn small ${chips.length ? "" : "secondary"}`} aria-expanded={moreOpen}
          aria-controls="dl-more" onClick={() => setMoreOpen(!moreOpen)}>Filters{chips.length ? ` (${chips.length})` : ""}</button>
      </form>
      {moreOpen && (
        <div id="dl-more" className="report-more">
          <div className="field"><label htmlFor="dl-result">Result</label>
            <select id="dl-result" value={params.get("result") ?? ""} onChange={(e) => update({ result: e.target.value })}>
              {RESULTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></div>
          <div className="field"><label htmlFor="dl-service">Processing or AI level</label>
            <select id="dl-service" value={params.get("service") ?? ""} onChange={(e) => update({ service: e.target.value })}>
              <option value="">Any</option>
              {services.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select></div>
          <div className="field"><label htmlFor="dl-audit">Remediated or audit</label>
            <select id="dl-audit" value={params.get("audit") ?? ""} onChange={(e) => update({ audit: e.target.value })}>
              {AUDIT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></div>
          <div className="field"><label htmlFor="dl-from">Submitted from</label>
            <input id="dl-from" type="date" value={params.get("from") ?? ""} onChange={(e) => update({ from: e.target.value })} /></div>
          <div className="field"><label htmlFor="dl-to">To</label>
            <input id="dl-to" type="date" value={params.get("to") ?? ""} onChange={(e) => update({ to: e.target.value })} /></div>
          <button type="button" className="linkbtn small report-more-done" onClick={() => setMoreOpen(false)}>Done</button>
        </div>
      )}
      {chips.length > 0 && (
        <ul className="report-chips" aria-label="Filters in use">
          {chips.map((c) => (
            <li key={c.key} className="chip">{c.text}
              <button type="button" aria-label={`Remove the filter ${c.text}`} onClick={() => update({ [c.key]: null })}>×</button>
            </li>
          ))}
        </ul>
      )}

      {docs.error ? <ErrorNotice error={docs.error} /> : null}
      {!data ? <Loading /> : (
        <>
          <p className="small muted documents-count" role="status" aria-live="polite">
            {data.total === 0 ? (filtering ? "Nothing matches." : "") : `${fmtNum(first)}–${fmtNum(last)} of ${fmtNum(data.total)} documents`}
            {filtering && <> · <button type="button" className="linkbtn small" onClick={() => {
              setSearch("");
              const keep = new URLSearchParams();
              if (params.get("size")) keep.set("size", params.get("size")!);
              setParams(keep, { replace: true });
            }}>Clear search and filters</button></>}
          </p>
          {doneMsg && <Notice>{doneMsg}</Notice>}
          {scope ? scope.bar?.(selected, done)
            : <MarkDoneBar selected={selected} holdHours={data.items[0]?.removal_hold_hours ?? 24} onDone={done} />}
          {(data.total > 0 || !filtering) && (
            <JobsTable jobs={data.items} sort={sort} showUser={!!scope} showService={!!scope}
              selection={scope && !scope.can ? undefined : { can: scope?.can ?? canMarkDone, selected, onChange: setSelected }} onSort={(key) => update({ sort: sort === key ? `-${key}` : sort === `-${key}` ? key
              : key === "created_at" ? "-created_at" : key }, true)}
              back={{ back: location.pathname + location.search, backLabel: scope?.backLabel ?? "My documents" }} />
          )}
          {data.total > Math.min(...SIZES) && (
            <nav className="pager" aria-label="Pages of documents">
              <button type="button" className="btn secondary small" disabled={page <= 1} onClick={() => update({ page: String(page - 1) }, true)}>
                ← Previous</button>
              <span className="small">Page {fmtNum(page)} of {fmtNum(pages)}</span>
              <button type="button" className="btn secondary small" disabled={page >= pages} onClick={() => update({ page: String(page + 1) }, true)}>
                Next →</button>
              <label className="small pager-size">Show
                <select value={size} onChange={(e) => update({ size: e.target.value === "25" ? null : e.target.value })}>
                  {SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                </select> per page</label>
            </nav>
          )}
        </>
      )}
    </div>
  );
}
