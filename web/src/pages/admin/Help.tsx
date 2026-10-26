import { useState } from "react";
import { useSeen } from "../../components/seen";

const always = () => true;
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import {
  get, post, postForm, type DocVersion, type Group, type HelpRequest, type HelpStatus, type HelpSummary, type Paged, type Versions,
} from "../../api";
import { useAuth } from "../../auth";
import { isStaffRole } from "../../permissions";
import { HELP_KINDS, HelpOutcome, HelpStatusPill, HelpThread, kindLabel } from "../../components/help";
import { ErrorNotice, Loading, fmtDate, fmtNum, useLoad, useTitle } from "../../components/ui";
import { useUnsavedChanges } from "../../components/unsaved";
import { ListBar, ListPager, SortHeading, type Chip } from "../../components/lists";

/** How long something has been waiting, short: "45 min", "3 h", "2 d". */
function age(since: string) {
  const mins = Math.max(0, (Date.now() - new Date(since).getTime()) / 60000);
  if (mins < 60) return `${Math.round(mins)} min`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h`;
  return `${Math.round(mins / 1440)} d`;
}

type Show = "needs_us" | "active" | HelpStatus | "all";
const SHOW: [Show, string][] = [
  ["needs_us", "Needs us"], ["active", "Not finished"], ["open", "Open"], ["in_progress", "In progress"],
  ["waiting", "Waiting on the person"], ["resolved", "Resolved"], ["closed", "Closed"], ["all", "Everything"],
];

type HelpPage = Paged<HelpSummary> & { needs_us: number };

export function HelpTable({ rows, showGroup = true, sort, onSort }: {
  rows: HelpSummary[]; showGroup?: boolean; sort: string; onSort: (sort: string) => void;
}) {
  if (rows.length === 0) return <p className="empty">No help requests here.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><SortHeading label="Document" sortKey="document" sort={sort} onSort={onSort} />
          <th scope="col">Person{showGroup ? " · group" : ""}</th><th scope="col">Kind</th>
          <th scope="col">Status</th><th scope="col">Handled by</th>
          <SortHeading label="Waiting" sortKey="waiting" sort={sort} onSort={onSort} className="num" /></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td><Link to={`/admin/help/${r.id}`}>{r.filename}</Link>
                <div className="muted small">{r.version_label ?? ""}{r.page ? ` · page ${r.page}` : ""}</div></td>
              <td>{r.user_email}{showGroup && <div className="muted small">{r.group_name ?? "No group"}</div>}</td>
              <td>{kindLabel(r.kind)}</td>
              <td><HelpStatusPill status={r.status} replied={r.needs_staff && r.status !== "open"} /></td>
              <td>{r.assignee_email ?? <span className="muted">nobody yet</span>}</td>
              <td className="num">{r.status === "resolved" || r.status === "closed" ? "–" : age(r.waiting_since)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The view of a list of help requests in the page's address (#514): search, filters, sort, page. */
function useHelpList(base: string, defaults: Record<string, string>) {
  const [params, setParams] = useSearchParams();
  const get_ = (k: string) => params.get(k) ?? defaults[k] ?? "";
  const page = Number(params.get("page")) || 1;
  const size = Number(params.get("size")) || 25;
  const api = new URLSearchParams();
  for (const [k, a] of [["saved", "saved"], ["show", "show"], ["kind", "kind"], ["group", "group_id"], ["q", "q"], ["sort", "sort"]]) {
    if (get_(k)) api.set(a, get_(k));
  }
  api.set("page", String(page));
  api.set("page_size", String(size));
  const list = useLoad(() => get<HelpPage>(`${base}?${api}`), [base, api.toString()], always, 30000);
  const update = (changes: Record<string, string | null>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v === null || v === "" || v === defaults[k]) next.delete(k); else next.set(k, v); }
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  };
  const clear = (keep: string[]) => {
    const next = new URLSearchParams();
    for (const k of ["size", "sort", ...keep]) if (params.get(k)) next.set(k, params.get(k)!);
    setParams(next, { replace: true });
  };
  const pager = (where: "top" | "bottom", topId: string) => list.data && (
    <ListPager page={page} pageSize={list.data.page_size} total={list.data.total} noun={["request", "requests"]} where={where}
      topId={topId} onPage={(n) => update({ page: n > 1 ? String(n) : null }, true)}
      onSize={(n) => update({ size: n === 25 ? null : String(n) })} />
  );
  return { params, value: get_, list, update, clear, pager };
}

/** Super admins: every help request, a page at a time, the longest wait first. */
export function HelpQueue() {
  useTitle("Help requests");
  const { value, list, update, clear, pager } = useHelpList("/admin/help/list", { show: "needs_us", sort: "waiting" });
  const groups = useLoad(() => get<Group[]>("/admin/groups"), []);
  const saved = value("saved") === "true";
  const groupName = (groups.data ?? []).find((g) => g.id === value("group"))?.name;
  const chips: Chip[] = value("group") ? [{ key: "group", text: groupName ?? "Group" }] : [];
  return (
    <>
      <h1>Help requests</h1>
      <div className="tabs" role="tablist" aria-label="Help request views">
        <button className="tab" role="tab" aria-selected={!saved} onClick={() => update({ saved: null })}>Active</button>
        <button className="tab" role="tab" aria-selected={saved} onClick={() => update({ saved: "true", show: null })}>Saved</button>
      </div>
      <div role="tabpanel">
        <ListBar label="Search and filter help requests" searchId="hq-q" query={value("q")} placeholder="Search by document or person"
          onQuery={(q) => update({ q: q || null })} chips={chips} active={!!value("kind") || (!saved && value("show") !== "needs_us")}
          onRemove={(k) => update({ [k]: null })} onClear={() => clear(["saved"])}
          more={<div className="field"><label htmlFor="hq-group">Group</label>
            <select id="hq-group" value={value("group")} onChange={(e) => update({ group: e.target.value })}>
              <option value="">Any</option>
              {(groups.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select></div>}>
          {!saved && <>
            <label htmlFor="hq-show" className="visually-hidden">Show</label>
            <select id="hq-show" className="report-select" value={value("show")} onChange={(e) => update({ show: e.target.value })}>
              {SHOW.map(([k, l]) => <option key={k} value={k}>{l}{k === "needs_us" && list.data ? ` (${list.data.needs_us})` : ""}</option>)}
            </select>
          </>}
          <label htmlFor="hq-kind" className="visually-hidden">Kind</label>
          <select id="hq-kind" className="report-select" value={value("kind")} onChange={(e) => update({ kind: e.target.value })}>
            <option value="">Any kind</option>
            {HELP_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </ListBar>
        {saved && <p className="muted small">Saved requests are kept past the documents' retention.</p>}
        {list.loading && !list.data ? <Loading /> : !list.data ? <ErrorNotice error={list.error} /> : <>
          <div id="hq-top">{pager("top", "hq-top")}</div>
          <HelpTable rows={list.data.items} sort={value("sort")} onSort={(v) => update({ sort: v }, true)} />
          {pager("bottom", "hq-top")}
        </>}
      </div>
    </>
  );
}

/** One request: the conversation, and (for super admins) taking it and changing its status. */
export function HelpDetail() {
  const { id = "" } = useParams();
  const location = useLocation();
  const { me, reloadMe } = useAuth();
  const req = useLoad(() => get<HelpRequest>(`/help/${id}`), [id], always, 15000);
  useSeen([id ? `help:${id}` : null]);
  const [error, setError] = useState<unknown>(null);
  useTitle(req.data ? `Help: ${req.data.filename}` : "Help request");
  if (req.loading && !req.data) return <Loading />;
  if (!req.data) return <ErrorNotice error={req.error} />;
  const r = req.data;
  const staff = isStaffRole(r.role);  // support: a super admin or a service admin
  const active = r.status === "open" || r.status === "in_progress" || r.status === "waiting";

  async function act(path: string, body: unknown) {
    setError(null);
    try {
      req.setData(await post<HelpRequest>(`/help/${id}/${path}`, body));
      void reloadMe();
    } catch (e) {
      setError(e);
    }
  }

  const viewer = r.job_id ? `/jobs/${r.job_id}/tags${r.version_number !== null ? `?v=${r.version_number}` : ""}` : null;
  return (
    <>
      <p className="small"><Link to={staff ? "/admin/help" : r.group_id ? `/admin/groups/${r.group_id}/help` : "/admin"}>
        ← {staff ? "Help requests" : "The group's help requests"}</Link></p>
      <div className="page-head">
        <div>
          <h1>{r.filename}</h1>
          <p className="sub">{r.user_email}{r.group_name ? ` · ${r.group_name}` : ""} · asked {fmtDate(r.created_at)}</p>
        </div>
        <HelpStatusPill status={r.status} replied={r.needs_staff && r.status !== "open"} />
      </div>
      <div className="help-layout">
        <section className="panel" aria-label="Conversation">
          <HelpThread req={r} onChange={(x) => { req.setData(x); void reloadMe(); }} label={staff ? `Reply to ${r.user_email}` : "Reply"} />
        </section>
        <aside className="panel help-facts" aria-label="About the request">
          <h2>Request</h2>
          <dl className="facts">
            <dt>Kind</dt><dd>{kindLabel(r.kind)}</dd>
            <dt>Version</dt><dd>{r.version_label ?? "–"}{r.page ? ` · page ${r.page}` : ""}</dd>
            {r.tag && <><dt>Tag</dt><dd><span className="mono">{r.tag.role}</span>{r.tag.text ? ` "${r.tag.text}"` : ""}</dd></>}
            <dt>Service</dt><dd>{r.processing ?? "–"}{r.ai_tier ? ` + ${r.ai_tier}` : ""}</dd>
            <dt>Handled by</dt><dd>{r.assignee_email ?? "nobody yet"}</dd>
          </dl>
          <div className="actions">
            {viewer && <Link className="btn secondary small" to={viewer}>Open in tag manager</Link>}
            {r.job_id && <Link className="btn secondary small" to={`/jobs/${r.job_id}`}
              state={{ back: location.pathname, backLabel: "the help request" }}>Open the document</Link>}
          </div>
          {staff && (
            <>
              <div className="actions">
                {r.assignee_email !== me?.email
                  ? <button className="btn small" onClick={() => void act("assign", { take: true })}>Take it</button>
                  : <button className="btn secondary small" onClick={() => void act("assign", { take: false })}>Let it go</button>}
              </div>
              {active && (
                <div className="field">
                  <span className="small"><strong>Status</strong></span>
                  <div className="actions">
                    {r.status !== "in_progress" && <button className="btn secondary small" onClick={() => void act("status", { status: "in_progress" })}>In progress</button>}
                    {r.status !== "waiting" && <button className="btn secondary small" onClick={() => void act("status", { status: "waiting" })}>Waiting on the person</button>}
                  </div>
                  <span className="muted small">The person is emailed when the status changes. Waiting on them: reminders after 3 and 7 days, closed at 10.</span>
                </div>
              )}
            </>
          )}
          {!active && <HelpOutcome req={r} />}
          {r.can_reopen && <button className="btn secondary small" onClick={() => void act("reopen", undefined)}>Reopen</button>}
          {staff && (
            <label className="check">
              <input type="checkbox" checked={r.saved} onChange={(e) => void act("save", { saved: e.target.checked })} />
              <span>Keep past retention <span className="muted small">(moves it to the Saved view)</span></span>
            </label>
          )}
          {staff && active && r.job_id && <CorrectedUpload r={r} onDone={(x) => req.setData(x)} />}
          {staff && active && <ResolvePanel r={r} onDone={(x) => { req.setData(x); void reloadMe(); }} />}
          {!staff && <p className="muted small">Support (super admins and service admins) handle the request and change its status; you can read and reply.</p>}
          <ErrorNotice error={error} />
        </aside>
      </div>
    </>
  );
}

/** A group's help requests, for its admins (and super admins). */
export function GroupHelp({ group }: { group: Group }) {
  const { value, list, update, clear, pager } = useHelpList(`/groups/${group.id}/help/list`, { show: "all", sort: "-updated" });
  return (
    <section className="panel" aria-labelledby="ghelp-h">
      <h2 id="ghelp-h">Help requests</h2>
      <p className="muted small">People in {group.name} asking for help with their documents. You can read and reply; super admins handle them.</p>
      <ListBar label="Search and filter the group's help requests" searchId="gh-q" query={value("q")} placeholder="Search by document or person"
        onQuery={(q) => update({ q: q || null })} chips={[]} active={!!value("kind") || value("show") !== "all"}
        onRemove={() => {}} onClear={() => clear([])}>
        <label htmlFor="gh-show" className="visually-hidden">Show</label>
        <select id="gh-show" className="report-select" value={value("show")} onChange={(e) => update({ show: e.target.value })}>
          {[...SHOW.filter(([k]) => k !== "all"), ["all", "Everything"] as [Show, string]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label htmlFor="gh-kind" className="visually-hidden">Kind</label>
        <select id="gh-kind" className="report-select" value={value("kind")} onChange={(e) => update({ kind: e.target.value })}>
          <option value="">Any kind</option>
          {HELP_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </ListBar>
      {list.loading && !list.data ? <Loading /> : !list.data ? <ErrorNotice error={list.error} /> : <>
        <div id="gh-top">{pager("top", "gh-top")}</div>
        <HelpTable rows={list.data.items} showGroup={false} sort={value("sort")} onSort={(v) => update({ sort: v }, true)} />
        {pager("bottom", "gh-top")}
      </>}
    </section>
  );
}

const checking = (v: DocVersion) => v.status === "queued" || v.status === "submitted" || v.status === "processing";

/** Upload a PDF corrected outside the portal: it becomes a checked version of the document. */
function CorrectedUpload({ r, onDone }: { r: HelpRequest; onDone: (x: HelpRequest) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(file !== null);
  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      onDone(await postForm<HelpRequest>(`/help/${r.id}/corrected`, form));
      setFile(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="help-tool">
      <summary>Upload a corrected PDF</summary>
      <p className="muted small">For a fix made outside the portal. It must have the same pages. It becomes "Corrected by support" and is checked
        like any saved version, including a visual comparison with {r.version_label ?? "the version it replaces"}.</p>
      <div className="field">
        <label htmlFor="corr-file">Corrected PDF</label>
        <input id="corr-file" type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </div>
      <ErrorNotice error={error} />
      <div className="actions"><button className="btn small" disabled={!file || busy} onClick={() => void upload()}>{busy ? "Uploading…" : "Upload"}</button></div>
    </details>
  );
}

/** Finish the request: what was done (sent to the person), the version that fixes it, and pages back. */
function ResolvePanel({ r, onDone }: { r: HelpRequest; onDone: (x: HelpRequest) => void }) {
  const versions = useLoad(() => (r.job_id ? get<Versions>(`/jobs/${r.job_id}/versions`) : Promise.resolve({ can_edit: false, versions: [] })),
    [r.job_id, r.messages.length], (d: Versions) => d.versions.some(checking), 5000);
  const choices = (versions.data?.versions ?? []).filter((v) => v.kind !== "original" && v.status !== "failed");
  const [note, setNote] = useState("");
  const [version, setVersion] = useState("");
  const [refund, setRefund] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(note.trim() !== "" || refund !== "");
  const chosen = choices.find((v) => String(v.number) === version);

  async function finish(outcome: "resolved" | "closed") {
    setBusy(true);
    setError(null);
    try {
      onDone(await post<HelpRequest>(`/help/${r.id}/resolve`, {
        outcome, note, version_number: version ? Number(version) : null, refund_pages: refund ? Number(refund) : null,
      }));
      setNote(""); setVersion(""); setRefund("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="help-resolve" aria-labelledby="resolve-h">
      <h3 id="resolve-h">Finish</h3>
      <div className="field">
        <label htmlFor="res-note">What was done <span className="hint">(sent to {r.user_email}; needed to resolve)</span></label>
        <textarea id="res-note" value={note} maxLength={5000} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="res-version">Version that fixes it <span className="hint">(optional)</span></label>
        <select id="res-version" value={version} onChange={(e) => setVersion(e.target.value)}>
          <option value="">None</option>
          {[...choices].reverse().map((v) => <option key={v.number} value={v.number}>{v.label}{checking(v) ? " (being checked)" : ""}</option>)}
        </select>
        {chosen && checking(chosen) && <span className="small muted">Still being checked; the person sees it once it's ready.</span>}
      </div>
      <div className="field" style={{ maxWidth: 220 }}>
        <label htmlFor="res-refund">Give pages back <span className="hint">(optional)</span></label>
        <input id="res-refund" type="number" min={1} max={r.pages_charged ?? undefined} value={refund} onChange={(e) => setRefund(e.target.value)} />
        {r.pages_charged !== null && <span className="small muted">The document cost {fmtNum(r.pages_charged)} pages.</span>}
      </div>
      <ErrorNotice error={error} />
      <div className="actions">
        <button className="btn small" disabled={busy || !note.trim()} onClick={() => void finish("resolved")}>Resolve</button>
        <button className="btn secondary small" disabled={busy} onClick={() => void finish("closed")}>Close without changes</button>
      </div>
    </section>
  );
}

