import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, del, get, patch, post, type Group, type LedgerEntry, type User } from "../../api";
import { LedgerTable } from "../../components/tables";
import { ErrorNotice, Notice, fmtNum, useLoad } from "../../components/ui";
import { useUnsavedChanges } from "../../components/unsaved";
import { GATEWAY_HELP } from "./gatewayHelp";

export /** The AI gateway's (LiteLLM's) admin UI: a link when the portal is configured with its address, and
 * otherwise how to reach it. A deployment that keeps the UI inside the cluster has no address to link, and a link
 * that isn't there (or goes nowhere) leaves a super admin without a way to find it. */
function GatewayLink({ children }: { children?: ReactNode }) {
  const links = useLoad(() => get<{ gateway_admin_url: string | null }>("/admin/links"), []);
  const [open, setOpen] = useState(false);
  if (!links.data) return null;
  const href = links.data.gateway_admin_url;
  const label = children ?? "AI gateway (LiteLLM)";
  if (href) {
    return (
      <a href={href} target="_blank" rel="noreferrer noopener" className="ext-link">
        {label} <span aria-hidden="true">↗</span>
        <span className="visually-hidden"> (opens in a new tab)</span>
      </a>
    );
  }
  return (
    <>
      <button type="button" className="link-button" aria-haspopup="dialog" onClick={() => setOpen(true)}>{label}</button>
      {open && <GatewayHelpDialog onClose={() => setOpen(false)} />}
    </>
  );
}

function GatewayHelpDialog({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    close.current?.focus();  // not the command's box, which a browser focuses first because it can scroll
  }, []);
  return (
    <dialog ref={ref} className="confirm-dialog gateway-help" aria-labelledby="gw-help-h" onClose={onClose}>
      <h2 id="gw-help-h">Opening the AI gateway</h2>
      <p>The gateway's own pages (every AI call, its cost and errors, models and keys) aren't published by this
        deployment, so there is no address to link to. Whoever runs the cluster opens them from their own machine:</p>
      <ol>
        <li>Run this, with the deployment's namespace, and leave it running:
          <pre><code>{GATEWAY_HELP.command}</code></pre></li>
        <li>Open <span className="mono">{GATEWAY_HELP.address}</span> in a browser on the same machine and sign in.</li>
      </ol>
      <p className="small muted">If port 4000 is taken there, use <span className="mono">{GATEWAY_HELP.otherPort}</span> and
        port 4001 in the address.</p>
      <p><a href={GATEWAY_HELP.guideUrl} target="_blank" rel="noreferrer noopener" className="ext-link">
        Operations guide: {GATEWAY_HELP.guideHeading.toLowerCase()} <span aria-hidden="true">↗</span>
        <span className="visually-hidden"> (opens in a new tab)</span></a></p>
      <div className="actions">
        <button ref={close} className="btn" type="button" onClick={() => ref.current?.close()}>Close</button>
      </div>
    </dialog>
  );
}

export type ServiceOpt = { key: string; label: string };

export function ServicePicker({ id, services, value, onChange }: { id: string; services?: ServiceOpt[]; value: string; onChange: (v: string) => void }) {
  if (!services || services.length < 2) return null;
  return (
    <div className="field">
      <label htmlFor={id}>Service</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {services.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
      </select>
    </div>
  );
}

export function Topup({ base, ledger: ledgerPath, onDone, services, fixedService, title = "Add pages" }: {
  base: string; ledger?: string; onDone: () => void; services?: ServiceOpt[]; fixedService?: string; title?: string;
}) {
  const [service, setService] = useState(fixedService ?? "standard");
  const u = base.replace(/[^a-z0-9]/gi, "-");
  const [pages, setPages] = useState("");
  const [ticket, setTicket] = useState("");
  const [ticketUrl, setTicketUrl] = useState("");
  const [override, setOverride] = useState(false);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<LedgerEntry | null>(null);
  useUnsavedChanges(!!(pages || ticket.trim() || ticketUrl.trim() || comment.trim()));
  const ledger = useLoad(() => (ledgerPath ? get<LedgerEntry[]>(ledgerPath) : Promise.resolve([] as LedgerEntry[])),
    [ledgerPath, done?.id]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const entry = await post<LedgerEntry>(`${base}/topups`, {
        service,
        pages: Number(pages),
        ticket_ref: ticket || null,
        ticket_url: ticketUrl || null,
        override,
        comment: comment || null,
      });
      setDone(entry);
      setPages(""); setTicket(""); setTicketUrl(""); setOverride(false); setComment("");
      onDone();
    } catch (err) {
      setError(err);
    }
  }

  const valid = Number(pages) > 0 && (override ? comment.trim().length > 0 : ticket.trim().length > 0);
  return (
    <section className="panel" aria-labelledby={`topup-h-${base}`}>
      <h3 id={`topup-h-${base}`}>{title}</h3>
      <form onSubmit={submit} className="panel" style={{ border: 0, padding: 0 }}>
        <div className="form-row">
          <ServicePicker id={`t-svc-${base}`} services={services} value={service} onChange={setService} />
          <div className="field">
            <label htmlFor={`t-pages-${u}`}>Pages to add</label>
            <input id={`t-pages-${u}`} type="number" min={1} step={1} required value={pages} onChange={(e) => setPages(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`t-ticket-${u}`}>Approving ticket number {override ? <span className="hint">(optional with override)</span> : null}</label>
            <input id={`t-ticket-${u}`} type="text" value={ticket} required={!override} onChange={(e) => setTicket(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`t-url-${u}`}>Ticket link <span className="hint">(optional)</span></label>
            <input id={`t-url-${u}`} type="url" placeholder="https://" value={ticketUrl} onChange={(e) => setTicketUrl(e.target.value)} />
          </div>
        </div>
        <label className="check">
          <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
          <span>Override: add pages without a ticket. <span className="muted">It will be flagged in reports.</span></span>
        </label>
        {override && (
          <div className="field">
            <label htmlFor={`t-comment-${u}`}>Why is there no ticket? <span className="hint">(required)</span></label>
            <textarea id={`t-comment-${u}`} required value={comment} onChange={(e) => setComment(e.target.value)} />
          </div>
        )}
        <div className="actions">
          <button className="btn" type="submit" disabled={!valid}>Add {pages ? fmtNum(Number(pages)) : ""} pages</button>
        </div>
      </form>
      <ErrorNotice error={error} />
      {done && (
        <Notice kind={done.override ? "warn" : "good"}>
          Added {fmtNum(done.pages)} pages{done.override ? " as an override" : ` under ticket ${done.ticket_ref}`}. The pool is now{" "}
          {fmtNum(done.pool_after)} pages.
        </Notice>
      )}
      {ledger.data && ledger.data.length > 0 && (
        <details>
          <summary>Previous top-ups</summary>
          <LedgerTable entries={ledger.data} />
        </details>
      )}
    </section>
  );
}

export function Adjust({ base, onDone, services, fixedService }: {
  base: string; onDone: () => void; services?: ServiceOpt[]; fixedService?: string;
}) {
  const [service, setService] = useState(fixedService ?? "standard");
  const u = base.replace(/[^a-z0-9]/gi, "-");
  const [pages, setPages] = useState("");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  useUnsavedChanges(!!(pages || comment.trim()));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const entry = await post<LedgerEntry>(`${base}/adjustments`, { service, pages: Number(pages), comment });
      setMsg(`Adjusted by ${entry.pages}. The pool is now ${fmtNum(entry.pool_after)} pages.`);
      setPages(""); setComment("");
      onDone();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <details className="panel">
      <summary>Correct the pool</summary>
      <p className="muted small">For mistakes only. Use a negative number to remove pages. The pool can't go below what's already used.</p>
      <form onSubmit={submit} className="form-row">
        <ServicePicker id={`a-svc-${base}`} services={services} value={service} onChange={setService} />
        <div className="field">
          <label htmlFor={`a-pages-${u}`}>Pages (+/-)</label>
          <input id={`a-pages-${u}`} type="number" step={1} required value={pages} onChange={(e) => setPages(e.target.value)} />
        </div>
        <div className="field" style={{ flex: "2 1 300px" }}>
          <label htmlFor={`a-comment-${u}`}>Reason <span className="hint">(required)</span></label>
          <input id={`a-comment-${u}`} type="text" required value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
        <button className="btn secondary" type="submit" disabled={!Number(pages) || !comment.trim()}>Apply</button>
      </form>
      <ErrorNotice error={error} />
      {msg && <Notice kind="good">{msg}</Notice>}
    </details>
  );
}

export function GroupSettings({ group, onSaved }: { group: Group; onSaved: (g: Group) => void }) {
  const [name, setName] = useState(group.name);
  const [cap, setCap] = useState(group.default_user_cap === null ? "" : String(group.default_user_cap));
  const [message, setMessage] = useState(group.over_quota_message ?? "");
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const dirty = name !== group.name || cap !== (group.default_user_cap === null ? "" : String(group.default_user_cap))
    || message !== (group.over_quota_message ?? "");
  useUnsavedChanges(dirty);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      onSaved(await patch<Group>(`/admin/groups/${group.id}`, {
        name,
        default_user_cap: cap === "" ? null : Number(cap),
        over_quota_message: message.trim() || null,
      }));
      setSaved(true);
    } catch (err) {
      setError(err);
    }
  }

  async function toggle() {
    const next = group.status === "active" ? "disabled" : "active";
    if (next === "disabled" && !window.confirm(`Disable ${group.name}? Its users are signed out and can't sign in.`)) return;
    try {
      onSaved(await patch<Group>(`/admin/groups/${group.id}`, { status: next }));
    } catch (err) {
      setError(err);
    }
  }

  return (
    <section className="panel" aria-labelledby="gset-h">
      <h3 id="gset-h">Group settings</h3>
      <form onSubmit={save} className="panel" style={{ border: 0, padding: 0 }}>
        <div className="form-row">
          <div className="field">
            <label htmlFor="gs-name">Name</label>
            <input id="gs-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="gs-cap">Default page limit per user <span className="hint">(empty = no limit)</span></label>
            <input id="gs-cap" type="number" min={0} value={cap} onChange={(e) => setCap(e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="gs-msg">Message when out of pages <span className="hint">(empty = the global message)</span></label>
          <textarea id="gs-msg" value={message} onChange={(e) => setMessage(e.target.value)} />
        </div>
        <div className="actions">
          <button className="btn" type="submit" disabled={!dirty}>Save</button>
          <button className={`btn ${group.status === "active" ? "danger" : "secondary"}`} type="button" onClick={() => void toggle()}>
            {group.status === "active" ? "Disable group" : "Enable group"}
          </button>
          {dirty && <span className="small unsaved-note">Unsaved changes</span>}
          {saved && !dirty && <span role="status" className="muted">Saved.</span>}
        </div>
      </form>
      <ErrorNotice error={error} />
    </section>
  );
}

export function Domains({ group, onChange }: { group: Group; onChange: (g: Group) => void }) {
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(domain.trim() !== "");

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onChange(await post<Group>(`/admin/groups/${group.id}/domains`, { domain }));
      setDomain("");
    } catch (err) {
      // A public email provider (gmail.com…): almost always one person's address was meant
      if (err instanceof ApiError && err.body.error === "public_domain") {
        if (!window.confirm(`${err.body.message}\n\nAllow everyone at ${domain.trim()} anyway?`)) return;
        try {
          onChange(await post<Group>(`/admin/groups/${group.id}/domains`, { domain, public_ok: true }));
          setDomain("");
        } catch (again) {
          setError(again);
        }
        return;
      }
      setError(err);
    }
  }
  async function remove(d: string) {
    if (!window.confirm(`Remove ${d}? New people from ${d} won't be able to sign up. Existing accounts keep working.`)) return;
    try {
      const g = await del<Group>(`/admin/groups/${group.id}/domains/${encodeURIComponent(d)}`);
      if (g) onChange(g);
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section className="panel" aria-labelledby="dom-h">
      <h3 id="dom-h">Email domains</h3>
      <p className="muted small">People who sign in with an address at one of these domains join this group.</p>
      <div className="chips">
        {group.domains.length === 0 && <span className="muted">None yet.</span>}
        {group.domains.map((d) => (
          <span key={d} className="chip">
            {d}
            <button type="button" aria-label={`Remove ${d}`} onClick={() => void remove(d)}>×</button>
          </span>
        ))}
      </div>
      <form onSubmit={add} className="form-row">
        <div className="field">
          <label htmlFor="dom-new">Add a domain</label>
          <input id="dom-new" type="text" placeholder="example.edu" value={domain} onChange={(e) => setDomain(e.target.value)} />
        </div>
        <button className="btn secondary" type="submit" disabled={!domain.trim()}>Add</button>
      </form>
      <ErrorNotice error={error} />
    </section>
  );
}

/** People allowed into the group by their address, without their whole domain (a gmail.com user, say). */
export function GroupEmails({ group, onChange }: { group: Group; onChange: (g: Group) => void }) {
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(email.trim() !== "");

  async function add(e: FormEvent, move = false) {
    e.preventDefault();
    setError(null);
    try {
      onChange(await post<Group>(`/admin/groups/${group.id}/emails`, { email, note: note.trim() || null, move }));
      setEmail("");
      setNote("");
    } catch (err) {
      if (!move && err instanceof ApiError && err.body.error === "email_taken" && window.confirm(err.body.message)) {
        await add(e, true);
        return;
      }
      setError(err);
    }
  }
  async function remove(addr: string, hasAccount: boolean) {
    const after = hasAccount
      ? "They've already signed in: their account keeps working (disable it in People to stop that)."
      : "They won't be able to sign up.";
    if (!window.confirm(`Remove ${addr}? ${after}`)) return;
    try {
      const g = await del<Group>(`/admin/groups/${group.id}/emails/${encodeURIComponent(addr)}`);
      if (g) onChange(g);
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section className="panel" aria-labelledby="gem-h">
      <h3 id="gem-h">People allowed by address</h3>
      <p className="muted small">Let one person in without allowing their whole domain, for example someone with a gmail.com
        address. An address here takes precedence over the email domains, so it can also place one person in this group
        apart from the rest of their organization.</p>
      {group.emails.length === 0 ? <p className="muted small">None yet.</p> : (
        <ul className="plain-list">
          {group.emails.map((e) => (
            <li key={e.email}>
              <strong>{e.email}</strong>{e.note && <span className="muted"> · {e.note}</span>}
              <span className="muted small"> · {e.has_account ? "signed in" : "hasn't signed in yet"}</span>{" "}
              <button type="button" className="linkbtn small" onClick={() => void remove(e.email, e.has_account)}>
                Remove<span className="visually-hidden"> {e.email}</span></button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={(e) => void add(e)} className="form-row">
        <div className="field">
          <label htmlFor="gem-new">Email address</label>
          <input id="gem-new" type="email" placeholder="person@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="gem-note">Note <span className="hint">(optional)</span></label>
          <input id="gem-note" type="text" maxLength={300} placeholder="Who they are" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <button className="btn secondary" type="submit" disabled={!email.trim()}>Add</button>
      </form>
      <ErrorNotice error={error} />
    </section>
  );
}

export function GroupAdmins({ group }: { group: Group }) {
  const admins = useLoad(() => get<User[]>(`/admin/groups/${group.id}/admins`), [group.id]);
  const [email, setEmail] = useState("");
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(email.trim() !== "");

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await post(`/admin/groups/${group.id}/admins`, { email });
      setEmail("");
      await admins.reload();
    } catch (err) {
      setError(err);
    }
  }
  async function remove(u: User) {
    try {
      await del(`/admin/groups/${group.id}/admins/${u.id}`);
      await admins.reload();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section className="panel" aria-labelledby="adm-h">
      <h3 id="adm-h">Group admins</h3>
      {admins.data && admins.data.length === 0 && <p className="muted">No admins yet.</p>}
      <ul>
        {admins.data?.map((u) => (
          <li key={u.id} className="actions">
            {u.email}{" "}
            <button className="linkbtn" onClick={() => void remove(u)}>Remove<span className="visually-hidden"> {u.email} as admin</span></button>
          </li>
        ))}
      </ul>
      <form onSubmit={add} className="form-row">
        <div className="field">
          <label htmlFor="adm-email">Make someone an admin <span className="hint">(their address must be in one of this group's domains)</span></label>
          <input id="adm-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <button className="btn secondary" type="submit" disabled={!email.includes("@")}>Add admin</button>
      </form>
      <ErrorNotice error={error} />
    </section>
  );
}

export function LimitInput({ id, label, value, placeholder, onSave }: {
  id: string; label: string; value: number | null; placeholder: string; onSave: (c: number | null) => void;
}) {
  const [v, setV] = useState(value === null ? "" : String(value));
  const dirty = v !== (value === null ? "" : String(value));
  useUnsavedChanges(dirty);
  return (
    <form className="actions" onSubmit={(e) => { e.preventDefault(); onSave(v === "" ? null : Number(v)); }}>
      <label htmlFor={id} className="visually-hidden">{label}</label>
      <input id={id} type="number" min={0} step={1} value={v} placeholder={placeholder} style={{ width: 130 }}
        onChange={(e) => setV(e.target.value)} />
      {dirty && <button className="btn small" type="submit">Save</button>}
    </form>
  );
}
