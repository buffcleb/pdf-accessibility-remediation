import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { download, get, type Group } from "../api";
import { useAuth } from "../auth";
import { can } from "../permissions";
import { useFullWidth } from "./fullwidth";
import { ReportChart, type ChartData } from "./ReportChart";
import { ScheduleView } from "./ScheduleView";
import { Dropdown } from "./Dropdown";
import { ErrorNotice, Loading, fmtNum, useLoad } from "./ui";
import { ListBar, ListPager } from "./lists";
import { apiQuery } from "./listQuery";

/* Live reports: the report, period, filters, search, sort, page and columns all live in the URL, so a view can
 * be bookmarked or sent to another admin (who sees it within their own rights). */

type Column = { key: string; label: string; kind: "text" | "num" | "money" | "date" | "bool" | "hours" | "period" | "pct"; total: boolean;
  link: "job" | "group" | "person" | "help" | null; shown: boolean };
type FilterDef = { key: string; label: string; options: { value: string; label: string }[] };
type ReportData = {
  name: string; label: string; description: string; search_hint: string; columns: Column[]; rows: Record<string, unknown>[];
  total_rows: number; page: number; page_size: number; totals: Record<string, number>;
  headline: { label: string; value: number | string; hint?: string }[]; sort: string; filters: FilterDef[];
  chart: ChartData | null; period: string | null;
};

/** The reports in three groups: [key, label, super admins only]. */
/** Dollars: cents for amounts of a dollar or more; below that, up to four decimals, so per-page AI costs
 * ($0.0019) don't show as $0.00. */
function fmtMoney(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "–";
  if (n === 0 || Math.abs(n) >= 1) return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `$${n.toFixed(4).replace(/(\.\d\d\d*?)0+$/, "$1")}`;
}

const REPORT_GROUPS: { label: string; superLabel?: string; reports: [string, string, boolean][] }[] = [
  { label: "Documents", reports: [["documents", "Documents", false], ["quality", "Quality", false], ["failures", "Remaining failures", false],
    ["limits", "Limits hit", true]] },
  { label: "Usage", superLabel: "Usage and cost", reports: [["usage", "Usage", false], ["ledger", "Page ledger", false], ["ai_usage", "AI usage", false],
    ["cost", "Cost", true], ["describe", "Describe with AI", true], ["daily", "Daily use", true]] },
  { label: "Activity", reports: [["sign_ins", "Sign-ins", false], ["email", "Email", false], ["help", "Help requests", false], ["audit", "Audit log", false],
    ["report_emails", "Report emails", true]] },
];

const RANGES: [string, string][] = [
  ["30d", "Last 30 days"], ["7d", "Last 7 days"], ["month", "This month"], ["last_month", "Last month"],
  ["year", "This year"], ["all", "All time"], ["custom", "Choose dates"],
];
/** Audit details as "key: value · key: old → new" rather than raw JSON. */
function describeDetails(raw: string): string {
  let d: Record<string, unknown>;
  try { d = JSON.parse(raw); } catch { return raw; }
  const bits: string[] = [];
  for (const [k, v] of Object.entries(d ?? {})) {
    if (v === null || v === undefined || v === "" || k === "by_super_admin" || k === "by_service_admin") continue;
    const val = Array.isArray(v) && v.length === 2 ? `${JSON.stringify(v[0])} → ${JSON.stringify(v[1])}` : JSON.stringify(v);
    bits.push(`${k}: ${val}`);
  }
  return bits.join(" · ");
}

/** Event names (version.review_requested) and email addresses may break after a dot or an @ rather than widen the table. */
function breakable(text: string) {
  return text.split(/(?<=[.@])/).map((part, i) => <Fragment key={i}>{i > 0 && <wbr />}{part}</Fragment>);
}

function cell(col: Column, row: Record<string, unknown>, labels: Record<string, Record<string, string>>, people: boolean,
  period: string, back: string) {
  const v = row[col.key];
  if (v === null || v === undefined || v === "") return col.kind === "num" && col.key === "limit" ? <span className="muted">No limit</span> : <span className="muted">–</span>;
  let text: string;
  switch (col.kind) {
    case "date": {
      const d = new Date(String(v));
      return <><span className="nowrap">{d.toLocaleDateString(undefined, { dateStyle: "medium" })}</span>{" "}
        <span className="muted small nowrap">{d.toLocaleTimeString(undefined, { timeStyle: "short" })}</span></>;
    }
    case "num": text = fmtNum(Number(v)); break;
    case "money": text = fmtMoney(v); break;
    case "hours": text = `${Number(v).toFixed(1)} h`; break;
    case "pct": text = `${Number(v)}%`; break;
    case "period": {
      const d = new Date(String(v));
      text = period === "month" ? d.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" })
        : period === "hour" ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC"
        : period === "week" ? `Week of ${d.toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" })}`
          : d.toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" });
      break;
    }
    case "bool": text = v ? "Yes" : "No"; break;
    default: text = labels[col.key]?.[String(v)] ?? String(v);
  }
  // A document's page keeps this view of the report in its address, for Back and for Previous and Next (#482)
  const to = col.link === "job" && row._job_id ? `/jobs/${row._job_id}?from=${encodeURIComponent(back)}`
    : col.link === "help" && row._help_id ? `/admin/help/${row._help_id}`
      : col.link === "group" && row._group_id ? `/admin/groups/${row._group_id}`
        : col.link === "person" && row._group_id ? `/admin/groups/${row._group_id}/people`
          : col.link === "person" && people ? "/admin/people" : null;
  if (col.key === "details") return <span className="report-details">{describeDetails(text)}</span>;
  const isEmail = /^[^\s@]+@[^\s@]+$/.test(text);
  const shownText = col.key === "event" ? breakable(text) : isEmail ? text.split(/(?<=@)/).map((p, i) => (
    <Fragment key={i}>{i > 0 && <wbr />}{p}</Fragment>)) : text;
  // A document's Back link returns to this exact view of the report
  return to ? <Link to={to} state={col.link === "job" ? { back, backLabel: "the report" } : undefined}>{shownText}</Link> : shownText;
}

/** The live reports, as tabs. `groupId`: fixed to one group. */
export function LiveReports({ groupId }: { groupId?: string }) {
  const { me } = useAuth();
  // Every report and column, for every group: super admins and service admins (#564)
  const isSuper = can(me, "reports.all");
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const name = params.get("report") ?? "documents";
  const query = apiQuery(params, name, groupId);
  const report = useLoad(() => get<ReportData>(query), [query]);
  const groups = useLoad(() => (isSuper && !groupId ? get<Group[]>("/admin/groups") : Promise.resolve([] as Group[])), [isSuper, groupId]);
  const [exportError, setExportError] = useState<unknown>(null);
  const [exporting, setExporting] = useState(false);

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  }

  const data = report.data;
  const labels = useMemo(() => Object.fromEntries((data?.filters ?? []).map((f) => [f.key, Object.fromEntries(f.options.map((o) => [o.value, o.label]))])), [data]);
  const shown = (data?.columns ?? []).filter((c) => c.shown);
  const range = params.get("range") ?? "30d";
  const periodKind = data?.chart?.x === "day" ? "day" : data?.period ?? "day";
  const sortKey = (data?.sort ?? "").replace(/^-/, "");
  const sortDesc = (data?.sort ?? "").startsWith("-");
  // The schedule form belongs to the report it was opened on: choosing another report closes it (each report has its
  // own schedule; the search and filters bar starts afresh for each report too)
  const [schedulingFor, setSchedulingFor] = useState<string | null>(null);
  const scheduling = schedulingFor === name;
  const setScheduling = (open: boolean) => setSchedulingFor(open ? name : null);
  useEffect(() => { setSchedulingFor(null); setExportError(null); }, [name]);
  // Pages of rows, above and below the table (#514)
  const pager = (where: "top" | "bottom") => data && (
    <ListPager page={data.page} pageSize={data.page_size} total={data.total_rows} noun={["row", "rows"]} where={where}
      topId="rp-top" onPage={(p) => update({ page: p > 1 ? String(p) : null })}
      onSize={(n) => update({ page_size: n === 25 ? null : String(n), page: null })} />
  );
  // Offer more room when the table is wider than its space
  const { full, setFull } = useFullWidth();
  const tableRef = useRef<HTMLDivElement>(null);
  const [tooWide, setTooWide] = useState(false);
  useEffect(() => {
    const el = tableRef.current;
    if (!el) { setTooWide(false); return; }
    const check = () => setTooWide(el.scrollWidth > el.clientWidth + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [data, shown.length]);
  // The report's own filters in use, as removable chips (search, period and group show in their own controls)
  // "By" (hour, day, week, month) sits beside the period; the rest are under Filters
  const byFilter = data?.filters.find((f) => f.key === "by");
  const otherFilters = (data?.filters ?? []).filter((f) => f.key !== "by");
  const chips = otherFilters.filter((f) => params.get(f.key)).map((f) => ({
    key: f.key, label: f.label, text: `${f.label}: ${labels[f.key]?.[params.get(f.key)!] ?? params.get(f.key)}` }));

  async function exportCsv() {
    setExporting(true);
    setExportError(null);
    try {
      const cols = params.get("cols") ?? shown.map((c) => c.key).join(",");
      const p = new URLSearchParams(params);
      p.set("cols", cols);
      await download(apiQuery(p, name, groupId, true), `${name}.csv`);
    } catch (e) {
      setExportError(e);
    } finally {
      setExporting(false);
    }
  }

  function toggleColumn(key: string, on: boolean) {
    const keys = new Set(shown.map((c) => c.key));
    if (on) keys.add(key);
    else keys.delete(key);
    const ordered = (data?.columns ?? []).filter((c) => keys.has(c.key)).map((c) => c.key);
    update({ cols: ordered.length ? ordered.join(",") : null });
  }

  return (
    <div className="live-report">
      {(() => {
        const keep = params.get("range") ? `&range=${params.get("range")}` : "";
        // Cost is for super admins only: group admins' charges aren't the per-page cost, so they don't see it
        const groups = REPORT_GROUPS.map((g) => ({ ...g, label: (isSuper && g.superLabel) || g.label,
          reports: g.reports.filter(([, , superOnly]) => isSuper || !superOnly) }));
        const current = groups.find((g) => g.reports.some(([key]) => key === name)) ?? groups[0];
        return (
          <>
            <nav className="tabs" aria-label="Report groups">
              {groups.map((g) => (
                <Link key={g.label} to={`?report=${g.reports[0][0]}${keep}`}
                  aria-current={g === current ? "true" : undefined}>{g.label}</Link>
              ))}
            </nav>
            <nav className="report-pills" aria-label={`${current.label} reports`}>
              {current.reports.map(([key, label]) => (
                <Link key={key} to={`?report=${key}${keep}`} className="pill-link"
                  aria-current={key === name ? "page" : undefined}>{label}</Link>
              ))}
            </nav>
          </>
        );
      })()}
      {data && <p className="muted small">{data.description}</p>}
      <ListBar key={name} label={`Search and filter ${data?.label ?? "the report"}`} searchId="rp-q" query={params.get("q") ?? ""}
        placeholder={data?.search_hint ? `Search ${data.search_hint}` : "Search"} onQuery={(q) => update({ q: q || null, page: null })}
        chips={chips} onRemove={(key) => update({ [key]: null, page: null })}
        onClear={() => {
          const keep = new URLSearchParams();
          for (const k of ["report", "range", "since", "until", "group_id", "cols", "page_size", "by", "sort"]) if (params.get(k)) keep.set(k, params.get(k)!);
          setParams(keep, { replace: true });
        }}
        more={data && otherFilters.length > 0 ? otherFilters.map((f) => (
          <div className="field" key={f.key}><label htmlFor={`rp-${f.key}`}>{f.label}</label>
            <select id={`rp-${f.key}`} value={params.get(f.key) ?? ""} onChange={(e) => update({ [f.key]: e.target.value, page: null })}>
              <option value="">Any</option>
              {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select></div>
        )) : undefined}>
        <label htmlFor="rp-range" className="visually-hidden">Period</label>
        <select id="rp-range" className="report-select" value={range}
          onChange={(e) => update({ range: e.target.value === "30d" ? null : e.target.value, page: null })}>
          {RANGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {byFilter && (
          <>
            <label htmlFor="rp-by" className="report-by-label">By</label>
            <select id="rp-by" className="report-select" value={params.get("by") ?? data?.period ?? ""}
              onChange={(e) => update({ by: e.target.value, page: null })}>
              {byFilter.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </>
        )}
        {isSuper && !groupId && (
          <>
            <label htmlFor="rp-group" className="visually-hidden">Group</label>
            <select id="rp-group" className="report-select" value={params.get("group_id") ?? ""}
              onChange={(e) => update({ group_id: e.target.value, page: null })}>
              <option value="">All groups</option>
              {(groups.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </>
        )}
      </ListBar>
      {range === "custom" && (
        <div className="report-dates">
          <div className="field"><label htmlFor="rp-since">From</label>
            <input id="rp-since" type="date" value={params.get("since") ?? ""} onChange={(e) => update({ since: e.target.value, page: null })} /></div>
          <div className="field"><label htmlFor="rp-until">To</label>
            <input id="rp-until" type="date" value={params.get("until") ?? ""} onChange={(e) => update({ until: e.target.value, page: null })} /></div>
        </div>
      )}

      {report.error ? <ErrorNotice error={report.error} /> : null}
      {!data ? <Loading /> : (
        <>
          {data.headline.length > 0 && (
            <section className="grid report-headline" aria-label="Summary">
              {data.headline.map((h) => (
                <div className="panel stat" key={h.label}>
                  <span className="num">{typeof h.value === "number" ? fmtNum(h.value) : h.value}</span>
                  <span className="lbl">{h.label}{h.hint && <span className="muted"> ({h.hint})</span>}</span>
                </div>
              ))}
            </section>
          )}
          {data.chart && <ReportChart chart={data.chart} period={periodKind} title={data.label} busy={report.loading} />}
          <div className="report-toolbar" id="rp-top">
            {/* The count is the pager's, below (#545): here only that it's updating */}
            <span role="status" aria-live="polite" className="small muted">{report.loading ? "Updating…" : ""}</span>
            <Dropdown className="report-columns" label="Columns" key={name}>{/* a new chooser, closed, for each report */}
              <fieldset className="choices">
                <legend className="visually-hidden">Columns to show</legend>
                {data.columns.map((c) => (
                  <label className="check" key={c.key}>
                    <input type="checkbox" checked={c.shown} onChange={(e) => toggleColumn(c.key, e.target.checked)} /><span>{c.label}</span>
                  </label>
                ))}
              </fieldset>
            </Dropdown>
            {(tooWide || full) && (
              <button className="btn secondary small" aria-pressed={full} onClick={() => setFull(!full)}
                title={full ? "Back to the normal page width" : "Use the full width of the window"}>
                {full ? "Narrow" : "Expand"}
              </button>
            )}
            <button className="btn secondary small" onClick={() => void exportCsv()} disabled={exporting || data.total_rows === 0}>
              {exporting ? "Exporting…" : "Export CSV"}
            </button>
            <button className="btn secondary small" aria-expanded={scheduling} onClick={() => setScheduling(!scheduling)}>
              Email this view
            </button>
          </div>
          {pager("top")}
          {scheduling && <ScheduleView report={name} label={data.label} params={params}
            groupId={groupId ?? params.get("group_id")} onClose={() => setScheduling(false)} />}
          <ErrorNotice error={exportError} />
          {data.rows.length === 0 ? <p className="empty">Nothing matches.</p> : (
            <div className="table-wrap" ref={tableRef}>
              <table>
                <caption className="visually-hidden">{data.label}, page {data.page} of {Math.max(1, Math.ceil(data.total_rows / data.page_size))}</caption>
                <thead>
                  <tr>
                    {shown.map((c) => {
                      const active = sortKey === c.key;
                      return (
                        <th key={c.key} scope="col" className={c.kind === "num" || c.kind === "money" || c.kind === "hours" ? "num" : undefined}
                          aria-sort={active ? (sortDesc ? "descending" : "ascending") : "none"}>
                          <button type="button" className="sort-btn" onClick={() => update({ sort: active && !sortDesc ? `-${c.key}` : c.key, page: null })}>
                            {c.label}<span aria-hidden="true">{active ? (sortDesc ? " ▼" : " ▲") : ""}</span>
                          </button>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, i) => (
                    <tr key={i}>
                      {shown.map((c) => (
                        <td key={c.key} className={c.kind === "num" || c.kind === "money" || c.kind === "hours" ? "num"
                          : c.kind === "bool" || c.link === "group" ? "nowrap" : c.kind === "date" ? "date-cell" : undefined}>
                          {cell(c, row, labels, can(me, "people"), periodKind, location.pathname + location.search)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
                {shown.some((c) => c.total) && (
                  <tfoot>
                    <tr>
                      {shown.map((c, i) => (
                        <td key={c.key} className={c.total ? "num" : undefined}>
                          {c.total ? (c.kind === "money" ? fmtMoney(data.totals[c.key] ?? 0) : fmtNum(data.totals[c.key] ?? 0))
                            : i === 0 ? <strong>Total, all {fmtNum(data.total_rows)} rows</strong> : null}
                        </td>
                      ))}
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
          {pager("bottom")}
        </>
      )}
    </div>
  );
}
