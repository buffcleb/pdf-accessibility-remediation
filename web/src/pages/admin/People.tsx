import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { del, download, get, patch, post, type Group, type Paged, type User } from "../../api";
import { useAuth } from "../../auth";
import { ErrorNotice, Loading, Notice, fmtDate, fmtNum, useLoad, useTitle } from "../../components/ui";
import { AdminDialog } from "../../components/AdminDialog";
import { ListBar, ListPager, SortHeading, type Chip } from "../../components/lists";
import { useUnsavedChanges } from "../../components/unsaved";
import { ServicePicker } from "./shared";

const ROLES: [string, string][] = [["super_admin", "Super admins"], ["group_admin", "Group admins"], ["member", "Members"]];
const STATUSES: [string, string][] = [["active", "Active"], ["disabled", "Disabled"], ["pending", "Waiting for approval"],
  ["declined", "Declined"], ["removed", "Data removed"]];

/** Everyone, across groups: move people, make super admins, disable accounts, correct usage. Limits are edited in
 * each group's People tab. */
export function PeopleAll() {
  useTitle("People");
  const { me, reloadMe } = useAuth();
  // The view lives in the address (#514): search, group, role, status, sort, page
  const [params, setParams] = useSearchParams();
  const sort = params.get("sort") ?? "email";
  const page = Number(params.get("page")) || 1;
  const size = Number(params.get("size")) || 25;
  const api = new URLSearchParams();
  for (const [k, a] of [["q", "q"], ["group", "group_id"], ["role", "role"], ["status", "status"]]) if (params.get(k)) api.set(a, params.get(k)!);
  api.set("sort", sort);
  api.set("page", String(page));
  api.set("page_size", String(size));
  const users = useLoad(() => get<Paged<User>>(`/admin/people?${api}`), [api.toString()]);
  const groups = useLoad(() => get<Group[]>("/admin/groups"), []);
  const update = (changes: Record<string, string | null>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v === null || v === "") next.delete(k); else next.set(k, v); }
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  };
  const label = (list: [string, string][], v: string | null) => list.find(([k]) => k === v)?.[1] ?? v ?? "";
  const chips: Chip[] = [
    params.get("role") ? { key: "role", text: label(ROLES, params.get("role")) } : null,
    params.get("status") ? { key: "status", text: label(STATUSES, params.get("status")) } : null,
  ].filter((c): c is Chip => c !== null);
  const pager = (where: "top" | "bottom") => users.data && (
    <ListPager page={page} pageSize={users.data.page_size} total={users.data.total} noun={["person", "people"]} where={where}
      topId="people-top" onPage={(n) => update({ page: n > 1 ? String(n) : null }, true)}
      onSize={(n) => update({ size: n === 25 ? null : String(n) })} />
  );
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await users.reload();
      void reloadMe();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <>
      <h1>People (all groups)</h1>
      <p className="muted">Personal page limits are set in each group's <strong>People</strong> tab; follow the group link. Open
        <strong> Manage</strong> to move someone, correct their usage, disable their account or make them a super admin.</p>
      <ListBar label="Search and filter people" searchId="uq" query={params.get("q") ?? ""} placeholder="Search by email or name"
        onQuery={(q) => update({ q: q || null })} chips={chips} active={!!params.get("group")}
        onRemove={(key) => update({ [key]: null })}
        onClear={() => { const keep = new URLSearchParams(); for (const k of ["size", "sort"]) if (params.get(k)) keep.set(k, params.get(k)!); setParams(keep, { replace: true }); }}
        more={<>
          <div className="field"><label htmlFor="uq-role">Role</label>
            <select id="uq-role" value={params.get("role") ?? ""} onChange={(e) => update({ role: e.target.value })}>
              <option value="">Any</option>
              {ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></div>
          <div className="field"><label htmlFor="uq-status">Status</label>
            <select id="uq-status" value={params.get("status") ?? ""} onChange={(e) => update({ status: e.target.value })}>
              <option value="">Any</option>
              {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></div>
        </>}>
        <label htmlFor="uq-group" className="visually-hidden">Group</label>
        <select id="uq-group" className="report-select" value={params.get("group") ?? ""} onChange={(e) => update({ group: e.target.value })}>
          <option value="">All groups</option>
          {(groups.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
      </ListBar>
      <ErrorNotice error={error} />
      {users.loading && !users.data ? <Loading /> : users.data && (
        <>
        <div id="people-top">{users.data.total === 0 ? <p className="small muted list-count" role="status">Nobody matches.</p> : pager("top")}</div>
        <div className="table-wrap">
          <table>
            <thead><tr>
              <SortHeading label="Person" sortKey="email" sort={sort} onSort={(v) => update({ sort: v === "email" ? null : v }, true)} />
              <SortHeading label="Group" sortKey="group" sort={sort} onSort={(v) => update({ sort: v }, true)} />
              <th scope="col" className="num">Standard pages used</th>
              <SortHeading label="Last sign-in" sortKey="last_login" sort={sort} onSort={(v) => update({ sort: v }, true)} />
              <th scope="col"><span className="visually-hidden">Manage</span></th></tr></thead>
            <tbody>
              {users.data.items.map((u) => {
                const isSuper = u.roles.some((r) => r.role === "super_admin");
                const group = groups.data?.find((g) => g.id === u.group_id);
                return (
                  <Fragment key={u.id}>
                    <tr>
                      <td>{u.email}{u.removed_at ? <> <span className="pill muted">Data removed</span></>
                        : u.status !== "active" && <> <span className="pill muted">Disabled</span></>}
                        {u.approval === "pending" && <> <span className="pill warn">Waiting for approval</span></>}
                        {u.approval === "declined" && <> <span className="pill warn">Declined</span></>}
                        {isSuper && <> <span className="pill info">Super admin</span></>}</td>
                      <td style={{ whiteSpace: "nowrap" }}>{u.group_id
                        ? <Link to={`/admin/groups/${u.group_id}/people`}>{group?.name ?? "Group"}</Link>
                        : <span className="muted">No group</span>}</td>
                      <td className="num">{fmtNum(u.used_pages)}</td>
                      <td className="small">{fmtDate(u.last_login_at)}</td>
                      <td>
                        <button className="btn secondary small" aria-haspopup="dialog" onClick={() => { setError(null); setOpen(u.id); }}>
                          Manage<span className="visually-hidden"> {u.email}</span>
                        </button>
                      </td>
                    </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        {pager("bottom")}
        </>
      )}
      {/* One person's account, group, pages and data, in the admin edit dialog (#511): each action takes effect at once */}
      {(() => {
        const u = users.data?.items.find((x) => x.id === open);
        if (!u) return null;
        const isSuper = u.roles.some((r) => r.role === "super_admin");
        return (
          <AdminDialog title={`Manage ${u.email}`} onClose={() => setOpen(null)}>
            <ErrorNotice error={error} />
            <div className="person-panel">
              {u.approval !== "approved" && u.group_id && (
                <Notice kind="warn" title={u.approval === "pending" ? "Waiting for approval" : "Declined"}>
                  {u.approval === "declined" && <p>{u.approval_by ? `By ${u.approval_by}` : "Declined"}{u.decline_reason ? `. Reason: ${u.decline_reason}` : "."}</p>}
                  <div className="actions">
                    <button className="btn small" onClick={() => void act(() => post(`/groups/${u.group_id}/users/${u.id}/approve`))}>Approve</button>
                    {u.approval === "pending" && <Link to={`/admin/groups/${u.group_id}/people`}>Decline with a reason in the group's People tab</Link>}
                  </div>
                </Notice>
              )}
              {u.removed_at && <p className="small muted">Their data was removed on {fmtDate(u.removed_at)}. The
                records stay, without them; nothing else can be changed here.</p>}
              {!u.removed_at && <MoveGroup user={u} groups={groups.data ?? []} onMove={(id) => act(() => patch(`/admin/users/${u.id}`, { group_id: id }))} />}
              {u.group_id && !u.removed_at && <UserPages user={u} onChange={(x) => users.data && users.setData({ ...users.data, items: users.data.items.map((y) => (y.id === x.id ? x : y)) })} />}
              {!u.removed_at && <PersonalData user={u} self={u.id === me?.id} onError={setError}
                onRemoved={() => void users.reload()} />}
              {u.id !== me?.id && !u.removed_at && (
                <div className="person-section">
                  <h3>Account</h3>
                  <div className="actions">
                    <button className={`btn small ${u.status === "active" ? "danger" : "secondary"}`}
                      onClick={() => void act(() => patch(`/admin/users/${u.id}`, { status: u.status === "active" ? "disabled" : "active" }))}>
                      {u.status === "active" ? "Disable account" : "Enable account"}
                    </button>
                    <button className="btn secondary small" onClick={() => void act(() => isSuper ? del(`/admin/users/${u.id}/super-admin`) : post(`/admin/users/${u.id}/super-admin`))}>
                      {isSuper ? "Remove super admin" : "Make super admin"}
                    </button>
                    <button className="btn secondary small" onClick={() => {
                      if (window.confirm(`Sign ${u.email} out of every browser and device? They'll need a new code to sign in.`)) {
                        void act(() => post(`/admin/users/${u.id}/sessions/end`));
                      }
                    }}>Sign out everywhere</button>
                  </div>
                </div>
              )}
            </div>
          </AdminDialog>
        );
      })()}
    </>
  );
}

/** A person's data on request (#338): exported for a records request, or removed. Removing keeps the service's
 * records (counts, pages, the audit trail) without their name, email or files, and can't be undone. */
function PersonalData({ user, self, onError, onRemoved }: {
  user: User; self: boolean; onError: (e: unknown) => void; onRemoved: () => void;
}) {
  const [asking, setAsking] = useState(false);
  async function exportData(files: boolean) {
    onError(null);
    try {
      await download(`/admin/users/${user.id}/export${files ? "?files=true" : ""}`, files ? "person.zip" : "person.json");
    } catch (e) {
      onError(e);
    }
  }
  return (
    <div className="person-section">
      <h3>Personal data</h3>
      <p className="small muted">For a records request. Exports and removals are recorded in the audit log.</p>
      <div className="actions">
        <button className="btn secondary small" onClick={() => void exportData(false)}>Export records</button>
        <button className="btn secondary small" onClick={() => void exportData(true)}>Export records and files</button>
        {!self && <button className="btn small danger" onClick={() => setAsking(true)}>Remove this person's data…</button>}
      </div>
      {asking && <RemoveDialog user={user} onClose={() => setAsking(false)} onError={onError}
        onDone={() => { setAsking(false); onRemoved(); }} />}
    </div>
  );
}

function RemoveDialog({ user, onClose, onError, onDone }: {
  user: User; onClose: () => void; onError: (e: unknown) => void; onDone: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  const ready = typed.trim().toLowerCase() === user.email.toLowerCase() && reason.trim().length >= 3;
  async function remove() {
    setBusy(true);
    onError(null);
    try {
      await post(`/admin/users/${user.id}/remove`, { confirm_email: typed.trim(), reason: reason.trim() });
      onDone();
    } catch (e) {
      onError(e);
      onClose();
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="rm-h" aria-describedby="rm-d"
      onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <h2 id="rm-h">Remove {user.email}'s data?</h2>
      <p id="rm-d">Their documents' files and help attachments are deleted, their name and email are replaced in every
        record, and their account is closed. The records themselves (documents, pages, the audit log) stay, without
        them, so reports still add up. This can't be undone; export their data first if they asked for a copy.</p>
      <div>
        <label htmlFor="rm-reason">Reason (kept in the audit log)</label>
        <input id="rm-reason" type="text" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <div>
        <label htmlFor="rm-email">Type their email address to confirm</label>
        <input id="rm-email" type="email" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
      </div>
      <div className="actions">
        <button className="btn danger" type="button" disabled={!ready || busy} onClick={() => void remove()}>
          {busy ? "Removing…" : "Remove their data"}</button>
        <button className="btn secondary" type="button" autoFocus onClick={onClose}>Cancel</button>
      </div>
    </dialog>
  );
}

function MoveGroup({ user, groups, onMove }: { user: User; groups: Group[]; onMove: (groupId: string | null) => Promise<void> }) {
  const [target, setTarget] = useState(user.group_id ?? "");
  useUnsavedChanges(target !== (user.group_id ?? ""));
  const name = groups.find((g) => g.id === target)?.name ?? "no group";

  async function move(e: FormEvent) {
    e.preventDefault();
    if (!window.confirm(`Move ${user.email} to ${name}? Their admin rights are removed; past usage stays with the old group.`)) return;
    await onMove(target || null);
  }
  return (
    <form className="form-row" onSubmit={move} aria-label={`Move ${user.email} to another group`}>
      <div className="field" style={{ maxWidth: 320 }}>
        <label htmlFor={`grp-${user.id}`}>Move to group</label>
        <select id={`grp-${user.id}`} value={target} onChange={(e) => setTarget(e.target.value)}>
          {user.group_id === null && <option value="">No group</option>}
          {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
      </div>
      <button className="btn secondary small" type="submit" disabled={target === (user.group_id ?? "")}>Move</button>
    </form>
  );
}

/** Corrections of what one person has used, per service. Audited. */
function UserPages({ user, onChange }: { user: User; onChange: (u: User) => void }) {
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [service, setService] = useState("standard");
  const [pages, setPages] = useState("");
  const [comment, setComment] = useState("");
  const [ticket, setTicket] = useState("");
  useUnsavedChanges(!!(pages || comment.trim() || ticket.trim()));
  const rows =user.services.filter((x) => x.granted || x.used_pages || x.reserved_pages || x.cap_pages !== null);

  async function correct(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const updated = await post<User>(`/admin/users/${user.id}/usage-corrections`, {
        service, pages: Number(pages), comment, ticket_ref: ticket || null,
      });
      onChange(updated);
      setMsg(`${Number(pages) < 0 ? "Gave back" : "Charged"} ${fmtNum(Math.abs(Number(pages)))} pages for ${user.email}.`);
      setPages(""); setComment(""); setTicket("");
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="person-section">
      <h3>Correct usage</h3>
      <p className="small">
        {rows.map((x) => `${x.label}: ${fmtNum(x.used_pages)} used${x.reserved_pages ? ` (+${fmtNum(x.reserved_pages)} in progress)` : ""}`).join(" · ") || "No usage yet."}
      </p>
      <form className="form-row" onSubmit={correct} aria-label={`Correct ${user.email}'s usage`}>
        <ServicePicker id={`c-svc-${user.id}`} services={rows.map((x) => ({ key: x.key, label: x.label }))} value={service} onChange={setService} />
        <div className="field" style={{ maxWidth: 170 }}>
          <label htmlFor={`c-pages-${user.id}`}>Pages <span className="hint">(− gives back)</span></label>
          <input id={`c-pages-${user.id}`} type="number" step={1} value={pages} onChange={(e) => setPages(e.target.value)} />
        </div>
        <div className="field" style={{ flex: "2 1 260px" }}>
          <label htmlFor={`c-why-${user.id}`}>Reason <span className="hint">(required, shown in the audit log)</span></label>
          <input id={`c-why-${user.id}`} type="text" value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
        <div className="field" style={{ maxWidth: 170 }}>
          <label htmlFor={`c-ticket-${user.id}`}>Ticket <span className="hint">(optional)</span></label>
          <input id={`c-ticket-${user.id}`} type="text" value={ticket} onChange={(e) => setTicket(e.target.value)} />
        </div>
        <button className="btn secondary small" type="submit" disabled={!Number(pages) || !comment.trim()}>Apply correction</button>
      </form>
      <p className="muted small">Corrections are recorded in the audit log.</p>
      <ErrorNotice error={error} />
      <div role="status" className="small">{msg}</div>
    </div>
  );
}
