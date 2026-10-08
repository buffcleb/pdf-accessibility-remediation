import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useSeen } from "./seen";
import { Link, useSearchParams } from "react-router-dom";
import {
  download, get, post, postForm, type HelpKind, type HelpMessage, type HelpRequest, type HelpStatus, type Job, type Versions,
} from "../api";
import { useAuth } from "../auth";
import { ErrorNotice, Loading, fmtDate, useLoad } from "./ui";
import { useUnsavedChanges } from "./unsaved";

export const HELP_KINDS: [HelpKind, string][] = [
  ["alt_text", "Alt text (descriptions of images)"],
  ["reading_order", "Reading order"],
  ["tables", "Tables"],
  ["headings", "Headings"],
  ["math", "Math and notation"],
  ["visual", "Looks different from the original"],
  ["other", "Something else"],
];
export const kindLabel = (k: HelpKind) => HELP_KINDS.find(([key]) => key === k)?.[1] ?? k;

const STAFF_STATUS: Record<HelpStatus, string> = {
  open: "Open", in_progress: "In progress", waiting: "Waiting on the person", resolved: "Resolved", closed: "Closed",
};
const PERSON_STATUS: Record<HelpStatus, string> = {
  open: "Received", in_progress: "Being looked at", waiting: "We need something from you", resolved: "Resolved", closed: "Closed",
};
const PILL: Record<HelpStatus, string> = { open: "info", in_progress: "warn", waiting: "note", resolved: "good", closed: "muted" };

/** A request's status, in the words of whoever's looking: the person, or the people helping them. */
export function HelpStatusPill({ status, forPerson = false, replied = false }: { status: HelpStatus; forPerson?: boolean; replied?: boolean }) {
  if (replied && !forPerson) return <span className="pill info">Replied</span>;
  return <span className={`pill ${PILL[status]}`}>{(forPerson ? PERSON_STATUS : STAFF_STATUS)[status]}</span>;
}

const ACCEPT = "application/pdf,image/png,image/jpeg,text/plain,.txt";

function FilePicker({ id, files, setFiles }: { id: string; files: File[]; setFiles: (f: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="field">
      <span className="small"><strong>Files</strong> <span className="hint">(PDF, PNG, JPEG or plain text; up to 5 at a time)</span></span>
      <div className="actions">
        {files.map((f, i) => (
          <span key={`${f.name}-${i}`} className="chip">{f.name}
            <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button>
          </span>
        ))}
        <label className="btn secondary small" htmlFor={id}>Add a file</label>
        <input ref={input} id={id} type="file" multiple accept={ACCEPT} className="visually-hidden"
          onChange={(e) => { setFiles([...files, ...Array.from(e.target.files ?? [])].slice(0, 5)); if (input.current) input.current.value = ""; }} />
      </div>
    </div>
  );
}

function Attachments({ request, message }: { request: string; message: HelpMessage }) {
  const [error, setError] = useState<unknown>(null);
  if (message.attachments.length === 0) return null;
  return (
    <div className="actions">
      {message.attachments.map((a) => (
        <button key={a.id} type="button" className="att" onClick={() => void download(`/help/${request}/attachments/${a.id}`, a.name).catch(setError)}>
          {a.name} <span className="muted">· {Math.max(1, Math.round(a.size_bytes / 1024))} KB</span>
        </button>
      ))}
      <ErrorNotice error={error} />
    </div>
  );
}

const ROLE_LABEL = { person: "", group_admin: "group admin", super_admin: "support", system: "" } as const;

/** The conversation, and a box to add to it. Super admins can also write internal notes. */
export function HelpThread({ req, onChange, label = "Reply" }: { req: HelpRequest; onChange: (r: HelpRequest) => void; label?: string }) {
  const { me } = useAuth();
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(body.trim() !== "" || files.length > 0);
  // When a refresh brings in someone else's message, say so to screen readers
  const [announce, setAnnounce] = useState("");
  const seen = useRef(req.messages.length);
  useEffect(() => {
    const fresh = req.messages.slice(seen.current).filter((m) => m.kind !== "event" && m.author_email !== me?.email);
    seen.current = req.messages.length;
    if (fresh.length) setAnnounce(`New message from ${fresh[fresh.length - 1].author_email}.`);
  }, [req.messages, me?.email]);
  const finished = req.status === "resolved" || req.status === "closed";
  const canWrite = !finished || req.role === "super_admin";

  async function send(internal: boolean) {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("body", body);
      if (internal) form.append("internal", "true");
      files.forEach((f) => form.append("files", f, f.name));
      onChange(await postForm<HelpRequest>(`/help/${req.id}/messages`, form));
      setBody("");
      setFiles([]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="help-thread">
      <div className="visually-hidden" role="status" aria-live="polite">{announce}</div>
      <ol className="thread" aria-label="Messages">
        {req.messages.map((m) => m.kind === "event" ? (
          <li key={m.id} className="event">{m.body} · {fmtDate(m.created_at)}</li>
        ) : (
          <li key={m.id} className={`msg${m.kind === "note" ? " note" : m.author_role === "super_admin" ? " staff" : ""}`}>
            <span className="who">
              <strong>{m.kind === "note" ? "Internal note" : m.author_email === me?.email ? "You" : m.author_email}</strong>
              {m.kind === "note" ? ` · ${m.author_email}` : ROLE_LABEL[m.author_role] ? ` (${ROLE_LABEL[m.author_role]})` : ""}
              {" · "}{fmtDate(m.created_at)}
            </span>
            {m.body && <p className="msg-body">{m.body}</p>}
            <Attachments request={req.id} message={m} />
          </li>
        ))}
      </ol>
      {canWrite ? (
        <form className="help-reply" onSubmit={(e: FormEvent) => { e.preventDefault(); void send(false); }}>
          <div className="field">
            <label htmlFor={`reply-${req.id}`}>{label}</label>
            <textarea id={`reply-${req.id}`} value={body} maxLength={5000} onChange={(e) => setBody(e.target.value)} />
          </div>
          <FilePicker id={`files-${req.id}`} files={files} setFiles={setFiles} />
          <ErrorNotice error={error} />
          <div className="actions">
            <button className="btn" type="submit" disabled={busy || (!body.trim() && files.length === 0)}>
              {req.role === "super_admin" ? `Send to ${req.user_email}` : "Send"}
            </button>
            {req.role === "super_admin" && (
              <button className="btn secondary" type="button" disabled={busy || !body.trim()} onClick={() => void send(true)}>
                Add as internal note
              </button>
            )}
            {req.role === "super_admin" && <span className="muted small">Internal notes are only seen by super admins.</span>}
          </div>
        </form>
      ) : (
        <p className="muted small">This request is {req.status}.{req.can_reopen
          ? " If it's still not right, reopen it to carry on here." : " If there's still a problem, ask for help with the document again."}</p>
      )}
    </div>
  );
}

/** How a finished request ended: what was done, the version that fixes it, pages given back. */
export function HelpOutcome({ req }: { req: HelpRequest }) {
  const forPerson = req.role === "person";
  const compare = req.job_id && req.resolved_version_number !== null && req.resolved_version_status === "ready"
    ? `/jobs/${req.job_id}/tags?left=${req.version_number ?? 1}&v=${req.resolved_version_number}` : null;
  return (
    <div className={`notice ${req.status === "resolved" ? "good" : "info"} help-outcome`}>
      <strong>{req.status === "resolved" ? "Resolved" : "Closed"}{req.finished_at ? ` · ${fmtDate(req.finished_at)}` : ""}</strong>
      {req.resolution && <p className="msg-body">{req.resolution}</p>}
      {req.resolved_version_label && (
        <p>
          {forPerson ? "The fixed version: " : "Version: "}<strong>{req.resolved_version_label}</strong>
          {req.resolved_version_status !== "ready" && req.resolved_version_status !== null && " (still being checked)"}
          {compare && <> · <Link to={compare}>Compare in the tag viewer</Link></>}
        </p>
      )}
      {!!req.pages_refunded && <p>{forPerson ? `${req.pages_refunded} pages were given back to you.` : `${req.pages_refunded} pages given back.`}</p>}
      {forPerson && req.reopen_until && req.can_reopen && <p className="small">Still not right? You can reopen this request until {fmtDate(req.reopen_until)}.</p>}
    </div>
  );
}

type Prefill = { version?: number; page?: number; tag?: { i: number; role: string; text: string } };

function AskForm({ job, prefill, onCreated, onCancel }: { job: Job; prefill: Prefill; onCreated: (r: HelpRequest) => void; onCancel: () => void }) {
  const versions = useLoad(() => get<Versions>(`/jobs/${job.id}/versions`), [job.id]);
  const usable = (versions.data?.versions ?? []).filter((v) => v.status === "ready" && v.kind !== "original");
  const [body, setBody] = useState("");
  const [kind, setKind] = useState<HelpKind>("other");
  const [version, setVersion] = useState<string>(prefill.version !== undefined ? String(prefill.version) : "");
  const [page, setPage] = useState(prefill.page ? String(prefill.page) : "");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(body.trim() !== "" || files.length > 0);
  const chosen = version || (usable.length ? String(usable[usable.length - 1].number) : "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("body", body);
      form.append("kind", kind);
      if (chosen) form.append("version", chosen);
      if (page) form.append("page", page);
      if (prefill.tag) form.append("tag", JSON.stringify(prefill.tag));
      files.forEach((f) => form.append("files", f, f.name));
      const r = await postForm<HelpRequest>(`/jobs/${job.id}/help`, form);
      setBody("");
      setFiles([]);
      onCreated(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel edit-panel" onSubmit={submit} aria-labelledby="ask-h">
      <h2 id="ask-h">Ask for help with this document</h2>
      <p className="muted small">A person who runs this service will look at it. You can add files and talk it through on the request.</p>
      {prefill.tag && <p className="small">About the tag you selected: <span className="mono">{prefill.tag.role}</span>{prefill.tag.text ? ` "${prefill.tag.text}"` : ""}{prefill.page ? `, page ${prefill.page}` : ""}.</p>}
      <div className="field">
        <label htmlFor="ask-body">What's wrong? <span className="hint">(required)</span></label>
        <textarea id="ask-body" required value={body} maxLength={5000} onChange={(e) => setBody(e.target.value)} autoFocus />
      </div>
      <div className="form-row">
        <div className="field"><label htmlFor="ask-kind">Kind of problem</label>
          <select id="ask-kind" value={kind} onChange={(e) => setKind(e.target.value as HelpKind)}>
            {HELP_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>
        <div className="field"><label htmlFor="ask-version">Which version</label>
          <select id="ask-version" value={chosen} onChange={(e) => setVersion(e.target.value)}>
            {usable.map((v) => <option key={v.number} value={v.number}>{v.label}</option>)}
          </select></div>
        <div className="field" style={{ maxWidth: 140 }}><label htmlFor="ask-page">Page <span className="hint">(optional)</span></label>
          <input id="ask-page" type="number" min={1} max={job.pages} value={page} onChange={(e) => setPage(e.target.value)} /></div>
      </div>
      <FilePicker id="ask-files" files={files} setFiles={setFiles} />
      <ErrorNotice error={error} />
      <div className="actions">
        <button className="btn" type="submit" disabled={busy || !body.trim()}>{busy ? "Sending…" : "Send request"}</button>
        <button className="btn secondary" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

/** On a document's page: its help request, or a way to ask for help. */
export function HelpPanel({ job }: { job: Job }) {
  const { me, reloadMe } = useAuth();
  const [params, setParams] = useSearchParams();
  // Keep the conversation fresh while it's going on
  const help = useLoad(() => get<HelpRequest | null>(`/jobs/${job.id}/help`), [job.id],
    useCallback((r: HelpRequest | null) => r !== null && ["open", "in_progress", "waiting"].includes(r.status), []), 15000);
  const [asking, setAsking] = useState(params.get("help") === "1");
  useSeen([help.data ? `help:${help.data.id}` : null]);
  const [actionError, setActionError] = useState<unknown>(null);
  const ref = useRef<HTMLDivElement>(null);
  const own = job.user_id === me?.id;
  const canAsk = own && job.status === "completed" && !job.files_deleted && !!me?.can_submit;
  const prefill: Prefill = {};
  if (params.get("version")) prefill.version = Number(params.get("version"));
  if (params.get("page")) prefill.page = Number(params.get("page"));
  try { if (params.get("tag")) prefill.tag = JSON.parse(params.get("tag")!); } catch { /* ignore a bad link */ }

  useEffect(() => {
    if (asking) ref.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [asking]);
  // Reading the request marks it seen: refresh the unread count
  useEffect(() => { if (help.data?.role === "person") void reloadMe(); }, [help.data?.id, help.data?.role, reloadMe]);

  if (help.loading && !help.data) return <Loading />;
  const req = help.data;
  const active = req && (req.status === "open" || req.status === "in_progress" || req.status === "waiting");
  const stopAsking = () => { setAsking(false); if (params.get("help")) setParams({}, { replace: true }); };

  return (
    <div ref={ref} className="help-panel">
      {req && (active || !asking) && (
        <section className="panel" aria-labelledby="help-h">
          <div className="page-head" style={{ alignItems: "center" }}>
            <h2 id="help-h">{own ? "Your help request" : "Help request"}</h2>
            <HelpStatusPill status={req.status} forPerson={req.role === "person"} replied={req.needs_staff && req.status !== "open"} />
          </div>
          <p className="muted small">
            {kindLabel(req.kind)}{req.version_label ? ` · ${req.version_label}` : ""}{req.page ? ` · page ${req.page}` : ""} · asked {fmtDate(req.created_at)}
            {req.role !== "person" && <> · <Link to={`/admin/help/${req.id}`}>open in Administration</Link></>}
          </p>
          {!active && <HelpOutcome req={req} />}
          <HelpThread req={req} onChange={(r) => help.setData(r)} />
          {!active && (
            <div className="actions">
              {req.can_reopen && req.role === "person" && (
                <button className="btn small" onClick={() => void post<HelpRequest>(`/help/${req.id}/reopen`).then((r) => help.setData(r)).catch(setActionError)}>
                  Reopen this request
                </button>
              )}
              {canAsk && <button className="btn secondary small" onClick={() => setAsking(true)}>Ask for help again</button>}
            </div>
          )}
          <ErrorNotice error={actionError} />
        </section>
      )}
      {!active && canAsk && (asking ? (
        <AskForm job={job} prefill={prefill} onCreated={(r) => { help.setData(r); stopAsking(); }} onCancel={stopAsking} />
      ) : !req && (
        <section className="panel help-invite" aria-labelledby="help-h">
          <div className="page-head" style={{ alignItems: "center" }}>
            <div>
              <h2 id="help-h">Need help with this document?</h2>
              <p className="muted small">If something in the result isn't right, tell us and a person will look at it.</p>
            </div>
            <button className="btn secondary" onClick={() => setAsking(true)}>Ask for help</button>
          </div>
        </section>
      ))}
    </div>
  );
}
