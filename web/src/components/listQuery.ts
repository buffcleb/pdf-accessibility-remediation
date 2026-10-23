// Lists of documents, as their addresses in the app say them and as the API takes them: for the lists themselves,
// and for a document's page, which keeps the list it was opened from in its address (?from=) and follows it with
// Previous and Next (#482). Run the tests with: npm test

const BASE = "http://app.invalid";  // only the path and query of an address in the app are read

const RESERVED = new Set(["report", "range", "since", "until", "q", "sort", "page", "page_size", "cols", "group_id"]);

/** The period as since/until (ISO), from a preset or custom dates (until is exclusive: the day after). */
function period(params: URLSearchParams): { since?: string; until?: string } {
  const range = params.get("range") ?? "30d";
  const now = new Date();
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  switch (range) {
    case "7d": return { since: new Date(day(now).getTime() - 6 * 86400000).toISOString() };
    case "30d": return { since: new Date(day(now).getTime() - 29 * 86400000).toISOString() };
    case "month": return { since: new Date(now.getFullYear(), now.getMonth(), 1).toISOString() };
    case "last_month": return { since: new Date(now.getFullYear(), now.getMonth() - 1, 1).toISOString(),
      until: new Date(now.getFullYear(), now.getMonth(), 1).toISOString() };
    case "year": return { since: new Date(now.getFullYear(), 0, 1).toISOString() };
    case "custom": {
      const s = params.get("since"), u = params.get("until");
      return { since: s ? new Date(`${s}T00:00:00`).toISOString() : undefined,
        until: u ? new Date(new Date(`${u}T00:00:00`).getTime() + 86400000).toISOString() : undefined };
    }
    default: return {};
  }
}

export function apiQuery(params: URLSearchParams, name: string, groupId?: string, forExport = false): string {
  const out = new URLSearchParams();
  const { since, until } = period(params);
  if (since) out.set("since", since);
  if (until) out.set("until", until);
  for (const [k, v] of params) {
    if (!RESERVED.has(k) && v) out.set(k, v);
  }
  for (const k of ["q", "sort", "cols"]) if (params.get(k) !== null) out.set(k, params.get(k)!);
  if (!forExport) {
    out.set("page", params.get("page") ?? "1");
    out.set("page_size", params.get("page_size") ?? "25");
  }
  const group = groupId ?? params.get("group_id");
  if (group) out.set("group_id", group);
  return `/reports/${name}${forExport ? ".csv" : ""}?${out.toString()}`;
}

/** The list's search, filters and sort, from its address, as the API takes them (the page aside): for the list, and
 * for a document's Previous and Next, which follow it (#482). */
export function documentsApiQuery(params: URLSearchParams): URLSearchParams {
  const api = new URLSearchParams();
  for (const k of ["q", "status", "result", "service", "audit", "via"]) if (params.get(k)) api.set(k, params.get(k)!);
  if (params.get("from")) api.set("since", new Date(`${params.get("from")}T00:00:00`).toISOString());
  if (params.get("to")) api.set("until", new Date(new Date(`${params.get("to")}T00:00:00`).getTime() + 86400000).toISOString());
  api.set("sort", params.get("sort") ?? "-created_at");
  return api;
}

/** The list a document was opened from: where the API lists it, what Back calls it, and which of its address's
 * parameters say the page and its size. */
export type ListSource = { api: string; query: string; label: string; pageKey: string; sizeKey: string };

/** The document page's ?from=, if it's a path in this app (never another site: it becomes the Back link). */
export function safeFrom(raw: string | null): string | null {
  return raw && raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\") ? raw : null;
}

/** The lists Previous and Next can follow, from their address: My documents, a group's documents, and the Documents
 * report (everyone's, or a group's). Anything else has no neighbours. */
export function listSource(from: string): ListSource | null {
  const url = new URL(from, BASE);
  const params = url.searchParams;
  if (url.pathname === "/") {
    return { api: "/documents", query: documentsApiQuery(params).toString(), label: "My documents", pageKey: "page", sizeKey: "size" };
  }
  const group = url.pathname.match(/^\/admin\/groups\/([0-9a-f-]+)\/documents$/);
  if (group) {
    return { api: `/groups/${group[1]}/documents`, query: documentsApiQuery(params).toString(), label: "the group's documents",
      pageKey: "page", sizeKey: "size" };
  }
  const report = url.pathname.match(/^\/admin(?:\/groups\/([0-9a-f-]+))?\/reports$/);
  if (report && (params.get("report") ?? "documents") === "documents") {
    const [api, query] = apiQuery(params, "documents", report[1]).split("?");
    return { api, query, label: "the report", pageKey: "page", sizeKey: "page_size" };
  }
  return null;
}

/** The list's address at the page that holds the document at `position`, so Back from it returns to the right page. */
export function atPage(from: string, source: ListSource, position: number): string {
  const url = new URL(from, BASE);
  const size = Number(url.searchParams.get(source.sizeKey)) || 25;
  const page = Math.ceil(position / size);
  if (page > 1) url.searchParams.set(source.pageKey, String(page)); else url.searchParams.delete(source.pageKey);
  return url.pathname + url.search;
}

/** The list's search, filters and sort as an export takes them (#481): everything the list shows, every page. */
export function exportFilters(params: URLSearchParams): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (const [k, v] of documentsApiQuery(params)) out[k] = k === "audit" ? v === "true" : v;
  return out;
}
