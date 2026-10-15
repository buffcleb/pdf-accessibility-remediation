import { Fragment, useState, type FormEvent } from "react";
import { Link, Navigate, NavLink, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  get, patch, post, put, type AccessCheck, type Group, type GroupService, type LedgerEntry, type User,
} from "../../api";
import { useAuth } from "../../auth";
import { AccessCheckForm, AccessCheckView, AttentionList, serviceOptions } from "../../components/attention";
import { DocumentsList } from "../../components/DocumentsList";
import { LiveReports } from "../../components/LiveReport";
import { AdminRemoveBar, canAdminRemove } from "../../components/removal";
import { LedgerTable } from "../../components/tables";
import { ErrorNotice, Loading, Meter, Notice, fmtDate, fmtNum, useLoad, useTitle } from "../../components/ui";
import { SAVED, useConfirmLeave, useUnsavedChanges } from "../../components/unsaved";
import { GroupHelp } from "./Help";
import { Adjust, Domains, GroupAdmins, GroupEmails, GroupSettings, LimitInput, Topup } from "./shared";

/** Super admins: every group, with what needs attention. Group admins: their groups (straight in if one). */
export function GroupsList() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const groups = useLoad(() => get<Group[]>(me?.is_super_admin ? "/admin/groups" : "/groups"), [me?.is_super_admin]);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [domains, setDomains] = useState("");
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(!!(name.trim() || domains.trim()));
  useTitle("Groups");
  if (groups.loading && !groups.data) return <Loading />;
  if (!groups.data) return <ErrorNotice error={groups.error} />;
  if (!me?.is_super_admin && groups.data.length === 1) return <Navigate to={`/admin/groups/${groups.data[0].id}`} replace />;

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const g = await post<Group>("/admin/groups", { name, domains: domains.split(/[\s,]+/).filter(Boolean) });
      navigate(`/admin/groups/${g.id}`, { state: SAVED });
    } catch (err) {
      setError(err);
    }
  }

  const list = groups.data.filter((g) => !q || g.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <h1>Groups</h1>
      <div className="field" style={{ maxWidth: 360 }}>
        <label htmlFor="g-q">Search groups</label>
        <input id="g-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th scope="col">Group</th><th scope="col">Email domains</th><th scope="col">Services on</th><th scope="col">Pages</th></tr></thead>
          <tbody>
            {list.map((g) => {
              const on = g.services.filter((x) => x.enabled);
              const empty = on.filter((x) => x.metered && x.people_with_access > 0 && x.balance.remaining_pages <= 0);
              return (
                <tr key={g.id}>
                  <td><Link to={`/admin/groups/${g.id}`}>{g.name}</Link>{g.status === "disabled" && <> <span className="pill muted">Disabled</span></>}
                    {g.pending_members > 0 && <> <Link className="pill warn" to={`/admin/groups/${g.id}/people`}>{g.pending_members} waiting</Link></>}</td>
                  <td className="small">{g.domains.join(", ") || <span className="muted">none</span>}</td>
                  <td className="small">{on.map((x) => x.label).join(", ") || <span className="muted">none</span>}</td>
                  <td>{empty.length > 0
                    ? <span className="pill bad">{empty.map((x) => x.label).join(", ")}: no pages</span>
                    : <span className="muted small">{fmtNum(g.balance.remaining_pages)} Standard pages left</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {me?.is_super_admin && (
        <details className="panel">
          <summary>Create a group</summary>
          <form onSubmit={create} className="form-row">
            <div className="field"><label htmlFor="g-name">Name</label>
              <input id="g-name" type="text" required value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div className="field"><label htmlFor="g-domains">Email domains <span className="hint">(comma-separated, e.g. example.org)</span></label>
              <input id="g-domains" type="text" value={domains} onChange={(e) => setDomains(e.target.value)} /></div>
            <button className="btn" type="submit" disabled={!name.trim()}>Create group</button>
          </form>
          <ErrorNotice error={error} />
        </details>
      )}
    </>
  );
}

/** One group's workspace, the same for super admins and the group's admins. */
export function GroupWorkspace() {
  const { groupId } = useParams();
  const { me } = useAuth();
  const group = useLoad(() => get<Group>(`/groups/${groupId}`), [groupId]);
  useTitle(group.data?.name ?? "Group");
  if (group.loading && !group.data) return <Loading />;
  if (!group.data) return <ErrorNotice error={group.error} />;
  const g = group.data;
  const base = `/admin/groups/${g.id}`;
  const isSuper = !!me?.is_super_admin;
  return (
    <>
      {isSuper && <p className="small"><Link to="/admin/groups">← All groups</Link></p>}
      <div className="page-head">
        <div>
          <h1>{g.name}</h1>
          <p className="sub">
            {g.domains.join(", ") || "No email domains"} · local-only {g.local_only ? "on" : "off"}
            {g.status === "disabled" && " · disabled"}
          </p>
        </div>
      </div>
      <nav className="tabs" aria-label="Group sections">
        <NavLink to={base} end>Overview</NavLink>
        <NavLink to={`${base}/services`}>Services and pages</NavLink>
        <NavLink to={`${base}/people`}>People{g.pending_members > 0 && <> <span className="badge">{g.pending_members}<span className="visually-hidden"> waiting for approval</span></span></>}</NavLink>
        <NavLink to={`${base}/help`}>Help requests{g.help_active > 0 && <> <span className="badge">{g.help_active}<span className="visually-hidden"> not finished</span></span></>}</NavLink>
        <NavLink to={`${base}/documents`}>Documents</NavLink>
        <NavLink to={`${base}/reports`}>Reports</NavLink>
        <NavLink to={`${base}/settings`}>Group settings</NavLink>
      </nav>
      <Routes>
        <Route index element={<Overview group={g} />} />
        <Route path="services" element={<ServicesAndPages group={g} isSuper={isSuper} onChange={(x) => group.setData(x)} reload={group.reload} />} />
        <Route path="people" element={<People group={g} onChange={() => void group.reload()} />} />
        <Route path="help" element={<GroupHelp group={g} />} />
        <Route path="documents" element={<Documents group={g} />} />
        <Route path="activity" element={<Navigate to={`${base}/reports?report=audit`} replace />} />
        <Route path="reports" element={<LiveReports groupId={g.id} />} />
        <Route path="settings" element={<GroupSettingsTab group={g} isSuper={isSuper} onChange={(x) => group.setData(x)} reload={group.reload} />} />
      </Routes>
    </>
  );
}

function Overview({ group }: { group: Group }) {
  const ledger = useLoad(() => get<LedgerEntry[]>(`/groups/${group.id}/ledger?limit=30`), [group.id]);
  const users = useLoad(() => get<User[]>(`/groups/${group.id}/users?limit=1000`), [group.id]);
  const emails = Object.fromEntries((users.data ?? []).map((u) => [u.id, u.email]));
  const metered = group.services.filter((x) => x.enabled && x.metered);
  return (
    <>
      <AttentionList path={`/groups/${group.id}/attention`} />
      <section className="grid" aria-label="Pages left">
        {metered.map((x) => (
          <div key={x.key} className={`panel stat${x.balance.remaining_pages <= 0 && x.people_with_access > 0 ? " stat-bad" : ""}`}>
            <span className="num">{fmtNum(x.balance.remaining_pages)}</span>
            <span className="lbl">{x.label} pages left of {fmtNum(x.balance.pool_pages)}</span>
            <Meter used={x.balance.used_pages + x.balance.reserved_pages} total={x.balance.pool_pages} label={`${x.label} pool`} />
          </div>
        ))}
      </section>
      <AccessCheckForm path={`/groups/${group.id}/access-check`} services={serviceOptions(group.services.filter((x) => x.enabled))} />
      <section className="panel" aria-labelledby="ledger-h">
        <h2 id="ledger-h">Page ledger</h2>
        {ledger.data ? <LedgerTable entries={ledger.data} emails={emails} /> : <Loading />}
      </section>
    </>
  );
}

// --- services and pages --------------------------------------------------------------------------

function forGroup(x: GroupService, g: Group) {
  if (x.key === "standard") return <span className="pill good">On</span>;
  if (!x.globally_enabled) return <span className="pill muted">Switched off for everyone</span>;
  if (x.external && g.local_only) return <span className="pill muted">Blocked: local-only</span>;
  return x.enabled ? <span className="pill good">On{x.is_default ? " (preselected)" : ""}</span> : <span className="pill muted">Off</span>;
}

/** One table for every service the group could use: who has it, the group's pages, the default limit, the exceptions. */
function ServicesAndPages({ group, isSuper, onChange, reload }: {
  group: Group; isSuper: boolean; onChange: (g: Group) => void; reload: () => void;
}) {
  const { config } = useAuth();
  const [open, setOpen] = useState<{ key: string; what: "edit" | "pages" } | null>(null);
  const confirmLeave = useConfirmLeave();
  // Closing or switching panels drops what's typed in the open one, so ask first (Cancel is the explicit discard).
  const toggle = async (key: string, what: "edit" | "pages") => {
    const next = open?.key === key && open.what === what ? null : { key, what };
    if (open && (await confirmLeave())) setOpen(next);
    else if (!open) setOpen(next);
  };
  return (
    <>
      <p className="muted">
        Every service the group can use, in one place. A person needs the service on for the group, to have it, and pages
        in the group's pool: a personal limit can't add pages the pool hasn't got.
      </p>
      <div className="table-wrap">
        <table className="svc-table">
          <thead>
            <tr><th scope="col">Service</th><th scope="col">For the group</th><th scope="col">Who has it</th>
              <th scope="col">Group's pages</th><th scope="col">Default per person</th><th scope="col">Own limits</th>
              <th scope="col"><span className="visually-hidden">Actions</span></th></tr>
          </thead>
          <tbody>
            {group.services.filter((x) => x.globally_enabled || x.enabled).map((x) => {
              const empty = x.enabled && x.metered && x.people_with_access > 0 && x.balance.remaining_pages <= 0;
              return (
                <tr key={x.key} className={empty ? "row-bad" : undefined}>
                  <td><strong>{x.label}</strong>
                    <div className="muted small">{x.kind === "ai_tier" ? "AI level" : "Processing"}{x.external ? " · external" : ""}</div></td>
                  <td>{forGroup(x, group)}</td>
                  <td>{!x.enabled ? "–" : x.all_users ? `Everyone (${x.people_with_access})` : `${x.people_with_access} chosen`}</td>
                  <td>
                    {!x.enabled && x.balance.pool_pages === 0 ? "–" : !x.metered ? <span className="muted">Counted only ({fmtNum(x.balance.used_pages)} used)</span> : (
                      <>
                        <div>{fmtNum(x.balance.remaining_pages)} of {fmtNum(x.balance.pool_pages)} left</div>
                        <Meter used={x.balance.used_pages + x.balance.reserved_pages} total={x.balance.pool_pages} label={`${x.label} pool`} />
                        {empty && <div className="small bad-text">Empty: everyone sees 0 left, whatever their limit</div>}
                      </>
                    )}
                  </td>
                  <td>{!x.metered || !x.enabled ? "–" : x.default_user_cap === null ? "No limit" : fmtNum(x.default_user_cap)}</td>
                  <td>{x.own_limits > 0
                    ? <Link to={`/admin/groups/${group.id}/people?service=${encodeURIComponent(x.key)}`}>{x.own_limits} {x.own_limits === 1 ? "person" : "people"}</Link>
                    : <span className="muted">none</span>}</td>
                  <td>
                    <div className="actions">
                      {x.metered && x.enabled && (
                        <button className={`btn small${empty ? "" : " secondary"}`} aria-expanded={open?.key === x.key && open.what === "pages"}
                          onClick={() => void toggle(x.key, "pages")}>
                          {isSuper ? "Add pages" : "Request pages"}<span className="visually-hidden"> for {x.label}</span>
                        </button>
                      )}
                      <button className="btn secondary small" aria-expanded={open?.key === x.key && open.what === "edit"}
                        onClick={() => void toggle(x.key, "edit")}>
                        Edit<span className="visually-hidden"> {x.label}</span>
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {open && (() => {
        const x = group.services.find((y) => y.key === open.key)!;
        if (open.what === "edit") return <ServiceForGroup key={x.key} group={group} svc={x} onSaved={(g) => { onChange(g); setOpen(null); }} onCancel={() => setOpen(null)} />;
        if (isSuper) return <Topup base={`/admin/groups/${group.id}`} ledger={`/groups/${group.id}/ledger?kind=topup&limit=20`} fixedService={x.key}
          onDone={reload} title={`Add ${x.label} pages to ${group.name}`} />;
        return (
          <section className="panel" aria-labelledby="req-h">
            <h2 id="req-h">More {x.label} pages for {group.name}</h2>
            <p>Super admins add pages to a group's pool, each under an approving ticket.</p>
            {config.supportUrl
              ? <p><a className="btn" href={config.supportUrl} target="_blank" rel="noreferrer noopener">Request pages<span className="visually-hidden"> (opens in a new tab)</span></a></p>
              : <p className="muted">Ask the people who run this service{config.contactEmail ? <> at <a href={`mailto:${config.contactEmail}`}>{config.contactEmail}</a></> : ""}.</p>}
            <p className="small muted">Say the group ({group.name}), the service ({x.label}) and how many pages.</p>
          </section>
        );
      })()}
    </>
  );
}

function ServiceForGroup({ group, svc, onSaved, onCancel }: { group: Group; svc: GroupService; onSaved: (g: Group) => void; onCancel: () => void }) {
  const [enabled, setEnabled] = useState(svc.enabled);
  const [everyone, setEveryone] = useState(svc.all_users);
  const [cap, setCap] = useState(svc.default_user_cap === null ? "" : String(svc.default_user_cap));
  const [preselect, setPreselect] = useState(svc.is_default);
  const [error, setError] = useState<unknown>(null);
  const standard = svc.key === "standard";
  const blocked = (svc.external && group.local_only) || !svc.globally_enabled;
  const dirty = enabled !== svc.enabled || everyone !== svc.all_users || preselect !== svc.is_default
    || cap !== (svc.default_user_cap === null ? "" : String(svc.default_user_cap));
  useUnsavedChanges(dirty);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const body: Record<string, unknown> = { enabled, all_users: everyone, default_user_cap: cap === "" ? null : Number(cap) };
      if (preselect && !svc.is_default) body.is_default = true;
      onSaved(await patch<Group>(`/groups/${group.id}/services/${encodeURIComponent(svc.key)}`, body));
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form className="panel edit-panel" onSubmit={save} aria-labelledby="sfg-h">
      <h2 id="sfg-h">{svc.label} for {group.name}</h2>
      <label className="check"><input type="checkbox" checked={enabled} disabled={standard || (!svc.enabled && blocked)} onChange={(e) => setEnabled(e.target.checked)} />
        <span>On for the group{blocked && !svc.enabled ? <span className="muted small"> ({!svc.globally_enabled ? "switched off for everyone" : "blocked: the group is local-only"})</span> : ""}</span></label>
      <fieldset className="choices" disabled={!enabled}>
        <legend>Who has it</legend>
        <label className="check"><input type="radio" name="who" checked={everyone} onChange={() => setEveryone(true)} /><span>Everyone in the group</span></label>
        <label className="check"><input type="radio" name="who" checked={!everyone} onChange={() => setEveryone(false)} />
          <span>Chosen people ({svc.people_with_access} now) <Link to={`/admin/groups/${group.id}/people?service=${encodeURIComponent(svc.key)}`}>choose in People</Link></span></label>
      </fieldset>
      {svc.metered && (
        <div className="field" style={{ maxWidth: 280 }}>
          <label htmlFor="sfg-cap">Default limit per person <span className="hint">(pages; empty = no personal limit)</span></label>
          <input id="sfg-cap" type="number" min={0} value={cap} onChange={(e) => setCap(e.target.value)} />
        </div>
      )}
      <label className="check"><input type="checkbox" checked={preselect} disabled={!enabled || svc.is_default} onChange={(e) => setPreselect(e.target.checked)} />
        <span>Preselected at upload {svc.kind === "ai_tier" ? "(the group's AI level)" : "(the group's processing option)"}</span></label>
      <div className="actions">
        <button className="btn" type="submit" disabled={!dirty}>Save</button>
        <button className="btn secondary" type="button" onClick={onCancel}>Cancel</button>
        {dirty && <span className="small unsaved-note">Unsaved changes</span>}
      </div>
      <ErrorNotice error={error} />
    </form>
  );
}

// --- people --------------------------------------------------------------------------------------

/** The group's people, with each one's access and limit for one service at a time: the only place limits are edited. */
function People({ group, onChange }: { group: Group; onChange: () => void }) {
  const { me, reloadMe } = useAuth();
  const [params, setParams] = useSearchParams();
  const enabled = group.services.filter((x) => x.enabled);
  const key = params.get("service") ?? enabled[0]?.key ?? "standard";
  const svc = group.services.find((x) => x.key === key) ?? enabled[0];
  const users = useLoad(() => get<User[]>(`/groups/${group.id}/users?limit=1000`), [group.id]);
  const [q, setQ] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState("");
  // One person's row opens a panel with the access check for the chosen service and the account actions
  const [open, setOpen] = useState<string | null>(null);
  const [why, setWhy] = useState<AccessCheck | null>(null);
  const [whyError, setWhyError] = useState<unknown>(null);

  async function access(u: User, body: { granted?: boolean; cap_pages?: number | null }, what: string) {
    setError(null);
    try {
      const updated = await put<User>(`/groups/${group.id}/users/${u.id}/services/${encodeURIComponent(svc.key)}`, body);
      users.setData((users.data ?? []).map((x) => (x.id === u.id ? updated : x)));
      setSaved(`${what} for ${u.email}.`);
      onChange();
      if (open === u.id) await check(u);
    } catch (e) {
      setError(e);
    }
  }

  async function describe(u: User, value: string) {
    setError(null);
    const on = value === "on" ? true : value === "off" ? false : null;
    try {
      const updated = await patch<User>(`/groups/${group.id}/users/${u.id}`, { ai_describe: on });
      users.setData((users.data ?? []).map((x) => (x.id === u.id ? updated : x)));
      setSaved(`Describe with AI ${on === null ? "follows the group" : on ? "is on" : "is off"} for ${u.email}.`);
    } catch (e) {
      setError(e);
    }
  }

  async function status(u: User) {
    setError(null);
    try {
      const updated = await patch<User>(`/groups/${group.id}/users/${u.id}`, { status: u.status === "active" ? "disabled" : "active" });
      users.setData((users.data ?? []).map((x) => (x.id === u.id ? updated : x)));
      setSaved(`${u.email} ${updated.status === "active" ? "enabled" : "disabled"}.`);
      await check(u);
    } catch (e) {
      setError(e);
    }
  }

  async function check(u: User, key = svc.key) {
    setWhy(null);
    setWhyError(null);
    try {
      setWhy(await get<AccessCheck>(`/groups/${group.id}/access-check?email=${encodeURIComponent(u.email)}&service=${encodeURIComponent(key)}`));
    } catch (e) {
      setWhyError(e);
    }
  }

  /** Approve or decline someone; the group's waiting count and the admin badge follow. */
  async function decide(u: User, action: "approve" | "decline", reason?: string) {
    setError(null);
    try {
      const updated = await post<User>(`/groups/${group.id}/users/${u.id}/${action}`, action === "decline" ? { reason: reason || null } : undefined);
      users.setData((users.data ?? []).map((x) => (x.id === u.id ? updated : x)));
      setSaved(`${u.email} ${action === "approve" ? "approved" : "declined"}.`);
      onChange();
      void reloadMe();
      if (open === u.id) await check(updated);
    } catch (e) {
      setError(e);
    }
  }

  function manage(u: User) {
    setError(null);
    if (open === u.id) { setOpen(null); return; }
    setOpen(u.id);
    void check(u);
  }

  if (!svc) return <p className="muted">No services are on for this group yet.</p>;
  const waiting = (users.data ?? []).filter((u) => u.approval === "pending");
  const list = (users.data ?? []).filter((u) => u.approval !== "pending"
    && (!q || u.email.includes(q.toLowerCase()) || (u.name ?? "").toLowerCase().includes(q.toLowerCase())));
  const def = svc.default_user_cap === null ? "No limit" : `Default ${fmtNum(svc.default_user_cap)}`;
  return (
    <>
    {waiting.length > 0 && <WaitingList people={waiting} onDecide={decide} />}
    <section className="panel" aria-labelledby="people-h">
      <h2 id="people-h">People</h2>
      <div className="form-row">
        <div className="field"><label htmlFor="p-q">Search</label>
          <input id="p-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="field"><label htmlFor="p-svc">Show access and limits for</label>
          <select id="p-svc" value={svc.key} onChange={(e) => {
            setParams({ service: e.target.value });
            const u = (users.data ?? []).find((x) => x.id === open);
            if (u) void check(u, e.target.value);
          }}>
            {enabled.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
          </select></div>
      </div>
      <p className="muted small">
        {svc.label}: {svc.all_users ? "everyone in the group has it" : "only the people ticked below have it"}.
        {svc.metered ? ` An empty limit means the group default (${svc.default_user_cap === null ? "no limit" : fmtNum(svc.default_user_cap)} pages).` : " Use is counted, never limited."}
      </p>
      <ErrorNotice error={error} />
      <div className="visually-hidden" role="status" aria-live="polite">{saved}</div>
      {users.loading && !users.data ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th scope="col">Person</th><th scope="col">Has it</th>
                {svc.metered && <th scope="col">Limit</th>}<th scope="col" className="num">Used</th>
                {svc.metered && <th scope="col" className="num">Left</th>}
                <th scope="col"><span className="visually-hidden">Manage</span></th></tr>
            </thead>
            <tbody>
              {list.map((u) => {
                const us = u.services.find((y) => y.key === svc.key);
                const admin = u.roles.some((r) => r.role === "group_admin" && r.group_id === group.id);
                const cols = 4 + (svc.metered ? 2 : 0);
                return (
                  <Fragment key={u.id}>
                    <tr className={open === u.id ? "row-open" : undefined}>
                      <td>
                        <div>{u.email}{u.status !== "active" && <> <span className="pill muted">Disabled</span></>}
                          {u.approval === "declined" && <> <span className="pill warn">Declined</span></>}
                          {admin && <> <span className="pill info">Group admin</span></>}
                          {u.roles.some((r) => r.role === "super_admin") && <> <span className="pill info">Super admin</span></>}</div>
                        <div className="muted small">Last sign-in: {fmtDate(u.last_login_at)}</div>
                      </td>
                      <td>{svc.all_users ? <span className="muted small">Yes (everyone)</span> : (
                        <label className="check">
                          <input type="checkbox" checked={!!us?.granted_individually}
                            onChange={(e) => void access(u, { granted: e.target.checked }, e.target.checked ? `${svc.label} given` : `${svc.label} removed`)} />
                          <span className="visually-hidden">{u.email} has {svc.label}</span>
                        </label>)}</td>
                      {svc.metered && <td><LimitInput id={`lim-${u.id}-${svc.key}`} label={`${svc.label} limit for ${u.email}`} value={us?.cap_pages ?? null}
                        placeholder={def} onSave={(c) => void access(u, { cap_pages: c }, `${svc.label} limit saved`)} /></td>}
                      <td className="num">{fmtNum(us?.used_pages ?? 0)}{us?.reserved_pages ? <span className="muted small"> +{fmtNum(us.reserved_pages)}</span> : null}</td>
                      {svc.metered && <td className="num">{u.approval !== "approved" && !admin ? "–"
                        : us?.remaining_pages === null || us?.remaining_pages === undefined ? "No limit" : fmtNum(us.remaining_pages)}</td>}
                      <td>
                        <button className="btn secondary small" aria-expanded={open === u.id} onClick={() => manage(u)}>
                          Manage<span className="visually-hidden"> {u.email}</span>
                        </button>
                      </td>
                    </tr>
                    {open === u.id && (
                      <tr className="row-panel"><td colSpan={cols}>
                        <div className="person-panel">
                          {u.approval === "declined" && (
                            <Notice kind="warn" title="Declined">
                              <p>{u.approval_by ? `By ${u.approval_by}` : "Declined"}{u.approval_at ? `, ${fmtDate(u.approval_at)}` : ""}.
                                {u.decline_reason ? ` Reason: ${u.decline_reason}` : ""}</p>
                              <p><button className="btn small" onClick={() => void decide(u, "approve")}>Approve {u.email}</button></p>
                            </Notice>
                          )}
                          <h3>Can {u.email} use {svc.label}?</h3>
                          {why ? <AccessCheckView result={why} /> : whyError ? <ErrorNotice error={whyError} /> : <Loading />}
                          <div className="person-section">
                            <h3>Describe with AI</h3>
                            <div className="field">
                              <label htmlFor={`d-${u.id}`} className="small">In the tag manager, for {u.email}</label>
                              <select id={`d-${u.id}`} value={u.ai_describe === true ? "on" : u.ai_describe === false ? "off" : "inherit"}
                                onChange={(e) => void describe(u, e.target.value)}>
                                <option value="inherit">Follow the group</option>
                                <option value="on">On</option>
                                <option value="off">Off</option>
                              </select>
                            </div>
                          </div>
                          {u.id !== me?.id && (
                            <div className="person-section">
                              <h3>Account</h3>
                              <div className="actions">
                                <button className={`btn small ${u.status === "active" ? "danger" : "secondary"}`} onClick={() => void status(u)}>
                                  {u.status === "active" ? "Disable account" : "Enable account"}
                                </button>
                                <span className="muted small">{u.status === "active" ? "Disabling signs them out and stops them signing in." : "Enabling lets them sign in again."}</span>
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
    </section>
    </>
  );
}

/** People who signed in for the first time and are waiting for an admin to let them submit documents. */
function WaitingList({ people, onDecide }: { people: User[]; onDecide: (u: User, action: "approve" | "decline", reason?: string) => Promise<void> }) {
  const [declining, setDeclining] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  useUnsavedChanges(declining !== null && reason.trim() !== "");
  return (
    <section className="panel waiting" aria-labelledby="waiting-h">
      <h2 id="waiting-h">Waiting for approval <span className="pill warn">{people.length}</span></h2>
      <p className="muted small">They can sign in, but can't submit documents until you approve them. They're emailed either way.</p>
      <ul className="waiting-list">
        {people.map((u) => (
          <li key={u.id}>
            <div>
              <strong>{u.email}</strong>{u.name && <span className="muted"> ({u.name})</span>}
              <div className="muted small">Signed in first {fmtDate(u.created_at)}</div>
            </div>
            {declining === u.id ? (
              <form className="form-row" onSubmit={(e) => { e.preventDefault(); void onDecide(u, "decline", reason.trim()).then(() => { setDeclining(null); setReason(""); }); }}>
                <div className="field">
                  <label htmlFor={`dr-${u.id}`}>Reason <span className="hint">(optional; they'll see it)</span></label>
                  <input id={`dr-${u.id}`} type="text" maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
                </div>
                <button className="btn danger small" type="submit">Decline</button>
                <button className="btn secondary small" type="button" onClick={() => { setDeclining(null); setReason(""); }}>Cancel</button>
              </form>
            ) : (
              <div className="actions">
                <button className="btn small" onClick={() => void onDecide(u, "approve")}>Approve<span className="visually-hidden"> {u.email}</span></button>
                <button className="btn secondary small" onClick={() => { setDeclining(u.id); setReason(""); }}>Decline…<span className="visually-hidden"> {u.email}</span></button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Documents({ group }: { group: Group }) {
  const { me } = useAuth();
  const isSuper = !!me?.is_super_admin;
  return (
    <section className="panel" aria-labelledby="gjobs-h">
      <h2 id="gjobs-h">Documents</h2>
      <p className="muted small">Opening someone else's files is recorded in the audit log (Reports).
        {isSuper && " Select documents to remove their files now or delete them."}</p>
      <DocumentsList key={group.id} scope={{
        endpoint: `/groups/${group.id}/documents`,
        services: group.services.filter((s) => s.enabled || s.balance.used_pages > 0).map((s) => ({ key: s.key, label: s.label })),
        backLabel: `${group.name}: documents`,
        ...(isSuper ? { can: canAdminRemove,
          bar: (selected: Set<string>, onDone: (message: string) => void) => <AdminRemoveBar selected={selected} onDone={onDone} /> } : {}),
      }} />
    </section>
  );
}

function GroupSettingsTab({ group, isSuper, onChange, reload }: { group: Group; isSuper: boolean; onChange: (g: Group) => void; reload: () => void }) {
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  async function localOnly(on: boolean) {
    setError(null);
    try {
      onChange(await patch<Group>(`/groups/${group.id}`, { local_only: on }));
      setMsg(on ? "The group is now local-only." : "Local-only is off.");
    } catch (e) {
      setError(e);
    }
  }
  async function showExternal(on: boolean) {
    setError(null);
    try {
      onChange(await patch<Group>(`/groups/${group.id}`, { show_external: on }));
      setMsg(on ? "External options are now marked on the upload form." : "External options are no longer marked.");
    } catch (e) {
      setError(e);
    }
  }
  async function describe(value: string) {
    setError(null);
    const on = value === "on" ? true : value === "off" ? false : null;
    try {
      onChange(await patch<Group>(`/groups/${group.id}`, { ai_describe: on }));
      setMsg(on === null ? "Describe with AI now follows the service default." : on ? "Describe with AI is on for the group." : "Describe with AI is off for the group.");
    } catch (e) {
      setError(e);
    }
  }
  async function requireApproval(on: boolean) {
    setError(null);
    try {
      onChange(await patch<Group>(`/groups/${group.id}`, { require_approval: on }));
      setMsg(on ? "New members now wait for approval." : "New members can submit documents straight away.");
    } catch (e) {
      setError(e);
    }
  }
  return (
    <>
      <section className="panel" aria-labelledby="approval-h">
        <h2 id="approval-h">New members</h2>
        <label className="check">
          <input type="checkbox" checked={group.require_approval} onChange={(e) => void requireApproval(e.target.checked)} />
          <span><strong>New members need approval.</strong> People who sign in for the first time can't submit documents
            until a group admin approves them. Turn it off if everyone in the group's email domains may use the service.</span>
        </label>
        {!group.require_approval && group.pending_members > 0 && (
          <p className="small">{group.pending_members} {group.pending_members === 1 ? "person is" : "people are"} still waiting from before:{" "}
            <Link to={`/admin/groups/${group.id}/people`}>approve or decline them</Link>.</p>
        )}
      </section>
      <AiChoice group={group} onChange={onChange} />
      <section className="panel" aria-labelledby="local-h">
        <h2 id="local-h">Where documents can go</h2>
        <label className="check">
          <input type="checkbox" checked={group.local_only} onChange={(e) => void localOnly(e.target.checked)} />
          <span><strong>Local only.</strong> Keep this group's documents on this service's own servers: services that send
            documents elsewhere can't be used, whatever else is switched on.</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={group.show_external} onChange={(e) => void showExternal(e.target.checked)} />
          <span><strong>Show which options are external.</strong> Mark the processing options and AI levels that send
            documents to an outside provider on the upload form.</span>
        </label>
        <div className="field">
          <label htmlFor="g-describe"><strong>Describe with AI</strong> in the tag manager</label>
          <select id="g-describe" value={group.ai_describe === true ? "on" : group.ai_describe === false ? "off" : "inherit"}
            onChange={(e) => void describe(e.target.value)}>
            <option value="inherit">Follow the service default</option>
            <option value="on">On for the group</option>
            <option value="off">Off for the group</option>
          </select>
          <span className="hint">Alt text for an area of a figure, written by the AI: one AI call at the document's AI level each
            time. Each person can also be set otherwise (People, then their row).</span>
        </div>
        <ErrorNotice error={error} />
        <div role="status" className="small muted">{msg}</div>
      </section>
      {isSuper ? (
        <>
          <GroupSettings group={group} onSaved={onChange} />
          <Domains group={group} onChange={onChange} />
          <GroupEmails group={group} onChange={onChange} />
          <GroupAdmins group={group} />
          <Adjust base={`/admin/groups/${group.id}`} onDone={reload} services={group.services.map((x) => ({ key: x.key, label: x.label }))} />
        </>
      ) : (
        <Notice kind="info">The group's name, email domains, admins and pages are managed by super admins.</Notice>
      )}
    </>
  );
}


/** Whether people pick the AI level and kind of document when they upload, or the group sets one for everything. */
function AiChoice({ group, onChange }: { group: Group; onChange: (g: Group) => void }) {
  const levels = group.services.filter((x) => x.kind === "ai_tier" && x.available);
  const [mode, setMode] = useState(group.ai_mode);
  const [level, setLevel] = useState(group.ai_level ?? levels.find((x) => x.is_default)?.key ?? levels[0]?.key ?? "");
  const [kind, setKind] = useState(group.ai_kind ?? "general");
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const kinds = levels.find((x) => x.key === level)?.kinds ?? [];
  const dirty = mode !== group.ai_mode || (mode === "fixed"
    && (level !== (group.ai_level ?? "") || kind !== (group.ai_kind ?? "general")));

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const body = mode === "fixed" ? { ai_mode: mode, ai_level: level, ai_kind: kind } : { ai_mode: mode };
      onChange(await patch<Group>(`/groups/${group.id}`, body));
      setMsg(mode === "fixed" ? "Everyone's documents now use the group's AI level." : "People now choose the AI level.");
    } catch (err) {
      setError(err);
    }
  }

  return (
    <section className="panel" aria-labelledby="ai-choice-h">
      <h2 id="ai-choice-h">AI for uploads</h2>
      {levels.length === 0 ? (
        <p className="muted">The group has no AI levels switched on, so there's nothing to choose.</p>
      ) : (
        <form onSubmit={(e) => void save(e)} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <fieldset className="choices">
            <legend>Who picks the AI level and kind of document?</legend>
            <label className="check">
              <input type="radio" name="ai-mode" checked={mode === "choose"} onChange={() => setMode("choose")} />
              <span><strong>People choose</strong> <span className="muted small">for each upload, and for single files.
                The group's default level is picked to start with.</span></span>
            </label>
            <label className="check">
              <input type="radio" name="ai-mode" checked={mode === "fixed"} onChange={() => setMode("fixed")} />
              <span><strong>One level for everything</strong> <span className="muted small">people aren't asked; every
                document uses the level and kind set here.</span></span>
            </label>
          </fieldset>
          {mode === "fixed" && (
            <div className="form-row">
              <div className="field"><label htmlFor="ai-fixed-level">AI level</label>
                <select id="ai-fixed-level" value={level} onChange={(e) => { setLevel(e.target.value); setKind("general"); }}>
                  {levels.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                </select></div>
              <div className="field"><label htmlFor="ai-fixed-kind">Kind of document</label>
                <select id="ai-fixed-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
                  <option value="general">General</option>
                  {kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
                </select></div>
            </div>
          )}
          <div className="actions">
            <button className="btn" type="submit" disabled={!dirty}>Save</button>
          </div>
          <ErrorNotice error={error} />
          <div role="status" className="small muted">{msg}</div>
        </form>
      )}
    </section>
  );
}
