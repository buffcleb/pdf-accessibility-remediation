import { Fragment, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { del, get, patch, post, type Group, type User } from "../../api";
import { useAuth } from "../../auth";
import { ErrorNotice, Loading, Notice, fmtDate, fmtNum, useLoad, useTitle } from "../../components/ui";
import { useUnsavedChanges } from "../../components/unsaved";
import { ServicePicker } from "./shared";

/** Everyone, across groups: move people, make super admins, disable accounts, correct usage. Limits are edited in
 * each group's People tab. */
export function PeopleAll() {
  useTitle("People");
  const { me, reloadMe } = useAuth();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const users = useLoad(() => get<User[]>(`/admin/users?limit=200${query ? `&q=${encodeURIComponent(query)}` : ""}`), [query]);
  const groups = useLoad(() => get<Group[]>("/admin/groups"), []);
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
      <form className="form-row" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
        <div className="field"><label htmlFor="uq">Search by email or name</label>
          <input id="uq" type="search" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <button className="btn secondary" type="submit">Search</button>
      </form>
      <ErrorNotice error={error} />
      {users.loading && !users.data ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Person</th><th scope="col">Group</th>
              <th scope="col" className="num">Standard pages used</th><th scope="col">Last sign-in</th>
              <th scope="col"><span className="visually-hidden">Manage</span></th></tr></thead>
            <tbody>
              {users.data?.map((u) => {
                const isSuper = u.roles.some((r) => r.role === "super_admin");
                const group = groups.data?.find((g) => g.id === u.group_id);
                return (
                  <Fragment key={u.id}>
                    <tr className={open === u.id ? "row-open" : undefined}>
                      <td>{u.email}{u.status !== "active" && <> <span className="pill muted">Disabled</span></>}
                        {u.approval === "pending" && <> <span className="pill warn">Waiting for approval</span></>}
                        {u.approval === "declined" && <> <span className="pill warn">Declined</span></>}
                        {isSuper && <> <span className="pill info">Super admin</span></>}</td>
                      <td style={{ whiteSpace: "nowrap" }}>{u.group_id
                        ? <Link to={`/admin/groups/${u.group_id}/people`}>{group?.name ?? "Group"}</Link>
                        : <span className="muted">No group</span>}</td>
                      <td className="num">{fmtNum(u.used_pages)}</td>
                      <td className="small">{fmtDate(u.last_login_at)}</td>
                      <td>
                        <button className="btn secondary small" aria-expanded={open === u.id} onClick={() => setOpen(open === u.id ? null : u.id)}>
                          Manage<span className="visually-hidden"> {u.email}</span>
                        </button>
                      </td>
                    </tr>
                    {open === u.id && (
                      <tr className="row-panel"><td colSpan={5}>
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
                          <MoveGroup user={u} groups={groups.data ?? []} onMove={(id) => act(() => patch(`/admin/users/${u.id}`, { group_id: id }))} />
                          {u.group_id && <UserPages user={u} onChange={(x) => users.setData((users.data ?? []).map((y) => (y.id === x.id ? x : y)))} />}
                          {u.id !== me?.id && (
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
                              </div>
                            </div>
                          )}
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
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
