import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { get, type Job } from "../api";
import { useAuth } from "../auth";
import { JobsTable, type Selection } from "./JobsTable";
import { documentsApiQuery } from "./listQuery";
import { ExportZip } from "./exportZip";
import { MarkDoneBar, SelectedBar, canMarkDone } from "./removal";
import { ErrorNotice, Loading, Notice, useLoad } from "./ui";
import { ListBar, ListPager, type Chip } from "./lists";

export type DocumentsPage = { items: Job[]; total: number; page: number; page_size: number; active: number };

const STATUSES: [string, string][] = [["", "All statuses"], ["held", "Waiting for you to confirm"], ["waiting", "Waiting"],
  ["in_progress", "In progress"], ["done", "Done"], ["failed", "Couldn't be processed"], ["canceled", "Canceled"]];
const RESULTS: [string, string][] = [["", "Any result"], ["passes", "Passes PDF/UA"], ["needs_work", "Needs work"],
  ["review", "Has items to review"], ["help", "Open help request"]];
const AUDIT: [string, string][] = [["", "Both"], ["false", "Remediated"], ["true", "Audit only"]];
const VIA: [string, string][] = [["", "Either"], ["script", "By a script (API token)"], ["web", "In the web app"]];

/** Another list of documents than the signed-in person's own: a group's, for its admins. */
export type DocumentsScope = {
  /** Where the page of documents comes from (answers like /documents) */
  endpoint: string;
  /** The services and AI levels offered in the filter */
  services: { key: string; label: string }[];
  /** What a document's page calls the way back to this list */
  backLabel: string;
  /** Which rows can be selected beyond those with files to download, and the bar that acts on the selection (given
   * the zip download to show in it) */
  can?: Selection["can"];
  bar?: (selected: Set<string>, onDone: (message: string) => void, extra: ReactNode) => ReactNode;
};

/** Documents, a page at a time, with search, filters and sorting: the person's own, or with `scope` a group's for
 * its admins (each row then says whose it is and how it was processed, and the search also finds a person). The
 * whole view lives in the address, so Back and bookmarks work. Refreshes itself while any document is still being
 * processed. */
export function DocumentsList({ onStatusChange, scope }: { onStatusChange?: (key: string) => void; scope?: DocumentsScope }) {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [doneMsg, setDoneMsg] = useState("");
  const size = Number(params.get("size")) || 25;
  const page = Number(params.get("page")) || 1;
  const sort = params.get("sort") ?? "-created_at";

  const api = documentsApiQuery(params);
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

  const services = scope?.services ?? (me?.services ?? []).filter((s) => s.available || s.used_pages > 0);
  const what = scope ? "the group's documents" : "your documents";
  const searchLabel = scope ? "Search by file name or person" : "Search by file name";
  const label = (list: [string, string][], v: string | null) => list.find(([k]) => k === v)?.[1] ?? v;
  const chips = [
    params.get("result") && { key: "result", text: label(RESULTS, params.get("result")) },
    params.get("service") && { key: "service", text: services.find((s) => s.key === params.get("service"))?.label ?? params.get("service") },
    params.get("audit") && { key: "audit", text: label(AUDIT, params.get("audit")) },
    params.get("via") && { key: "via", text: label(VIA, params.get("via")) },
    params.get("from") && { key: "from", text: `From ${params.get("from")}` },
    params.get("to") && { key: "to", text: `To ${params.get("to")}` },
  ].filter(Boolean) as Chip[];
  const filtering = !!(params.get("q") || params.get("status") || chips.length);
  const data = docs.data;
  const done = (message: string) => {
    setSelected(new Set());
    setDoneMsg(message);
    if (message) void docs.reload();
  };
  // The chosen documents' files as one zip (#481); never everything by default (#501)
  const exportSelected = <ExportZip endpoint={endpoint} request={{ ids: [...selected] }} label="Download as a zip" />;
  const pager = (where: "top" | "bottom") => data && (
    <ListPager page={page} pageSize={data.page_size} total={data.total} noun={["document", "documents"]} where={where}
      topId="dl-top" onPage={(p) => update({ page: p > 1 ? String(p) : null }, true)}
      onSize={(n) => update({ size: n === 25 ? null : String(n) })} />
  );

  return (
    <div className="documents-list">
      <ListBar label={`Search and filter ${what}`} searchId="dl-q" query={params.get("q") ?? ""} placeholder={searchLabel}
        onQuery={(q) => update({ q: q || null })} chips={chips} active={!!params.get("status")}
        onRemove={(key) => update({ [key]: null })}
        onClear={() => {
          const keep = new URLSearchParams();
          for (const k of ["size", "sort"]) if (params.get(k)) keep.set(k, params.get(k)!);
          setParams(keep, { replace: true });
        }}
        more={<>
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
          <div className="field"><label htmlFor="dl-via">Sent</label>
            <select id="dl-via" value={params.get("via") ?? ""} onChange={(e) => update({ via: e.target.value })}>
              {VIA.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></div>
          <div className="field"><label htmlFor="dl-from">Submitted from</label>
            <input id="dl-from" type="date" value={params.get("from") ?? ""} onChange={(e) => update({ from: e.target.value })} /></div>
          <div className="field"><label htmlFor="dl-to">To</label>
            <input id="dl-to" type="date" value={params.get("to") ?? ""} onChange={(e) => update({ to: e.target.value })} /></div>
        </>}>
        <label htmlFor="dl-status" className="visually-hidden">Status</label>
        <select id="dl-status" className="report-select" value={params.get("status") ?? ""} onChange={(e) => update({ status: e.target.value })}>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </ListBar>

      {docs.error ? <ErrorNotice error={docs.error} /> : null}
      {!data ? <Loading /> : (
        <>
          {/* With more than one page, the pager above the list says where you are */}
          <div id="dl-top">{data.total === 0
            ? <p className="small muted list-count" role="status">{filtering ? "Nothing matches." : ""}</p> : pager("top")}</div>
          {doneMsg && <Notice>{doneMsg}</Notice>}
          {scope ? (scope.bar ? scope.bar(selected, done, exportSelected)
            : <SelectedBar selected={selected} onDone={done}>{exportSelected}</SelectedBar>)
            : <MarkDoneBar selected={selected} holdHours={data.items[0]?.removal_hold_hours ?? 24} onDone={done} extra={exportSelected} />}
          {(data.total > 0 || !filtering) && (
            <JobsTable jobs={data.items} sort={sort} showUser={!!scope} showService={!!scope}
              selection={{ can: (j) => !j.files_deleted || (scope?.can ?? canMarkDone)(j), selected, onChange: setSelected }} onSort={(key) => update({ sort: sort === key ? `-${key}` : sort === `-${key}` ? key
              : key === "created_at" ? "-created_at" : key }, true)}
              back={{ back: location.pathname + location.search, backLabel: scope?.backLabel ?? "My documents" }} />
          )}
          {pager("bottom")}
        </>
      )}
    </div>
  );
}
