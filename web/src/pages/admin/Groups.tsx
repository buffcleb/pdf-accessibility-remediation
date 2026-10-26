import { Fragment, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  get, patch, post, put, PDFUA_LABELS, type AccessCheck, type Balance, type Group, type GroupService, type LedgerEntry, type Paged,
  type PdfUa, type User,
} from "../../api";
import { useAuth } from "../../auth";
import { AccessCheckForm, AccessCheckView, AttentionList, serviceOptions } from "../../components/attention";
import { DocumentsList } from "../../components/DocumentsList";
import { LiveReports } from "../../components/LiveReport";
import { AdminRemoveBar, canAdminRemove } from "../../components/removal";
import { ListBar, ListPager, SortHeading, type Chip } from "../../components/lists";
import { LedgerTable } from "../../components/tables";
import { ErrorNotice, Loading, Meter, Notice, fmtDate, fmtNum, useLoad, useTitle } from "../../components/ui";
import { AdminDialog, DialogButton } from "../../components/AdminDialog";
import { GroupApiTokens } from "../../components/ApiTokens";
import { SAVED, useUnsavedChanges } from "../../components/unsaved";
import { acrossGroups, can, roleLabel } from "../../permissions";
import { GroupHelp } from "./Help";
import { Adjust, Domains, GroupAdmins, GroupEmails, GroupSettings, LimitInput, Topup } from "./shared";

/** Super admins: every group, with what needs attention. Group admins: their groups (straight in if one). */
export function GroupsList() {
  const { me } = useAuth();
  const navigate = useNavigate();
  // A page at a time, the view in the address (#514): search by name or domain, status, sort
  const [params, setParams] = useSearchParams();
  const sort = params.get("sort") ?? "name";
  const page = Number(params.get("page")) || 1;
  const size = Number(params.get("size")) || 25;
  const api = new URLSearchParams();
  if (params.get("q")) api.set("q", params.get("q")!);
  if (params.get("status")) api.set("status", params.get("status")!);
  api.set("sort", sort);
  api.set("page", String(page));
  api.set("page_size", String(size));
  const groups = useLoad(() => get<Paged<Group>>(`/groups/browse?${api}`), [api.toString()]);
  useTitle("Groups");
  const update = (changes: Record<string, string | null>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v === null || v === "") next.delete(k); else next.set(k, v); }
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  };
  if (groups.loading && !groups.data) return <Loading />;
  if (!groups.data) return <ErrorNotice error={groups.error} />;
  const filtered = !!(params.get("q") || params.get("status"));
  if (!acrossGroups(me) && !filtered && groups.data.total === 1) return <Navigate to={`/admin/groups/${groups.data.items[0].id}`} replace />;

  const list = groups.data.items;
  const pager = (where: "top" | "bottom") => (
    <ListPager page={page} pageSize={groups.data!.page_size} total={groups.data!.total} noun={["group", "groups"]} where={where}
      topId="groups-top" onPage={(n) => update({ page: n > 1 ? String(n) : null }, true)}
      onSize={(n) => update({ size: n === 25 ? null : String(n) })} />
  );
  return (
    <>
      <h1>Groups</h1>
      <ListBar label="Search and filter groups" searchId="g-q" query={params.get("q") ?? ""} placeholder="Search by name or email domain"
        onQuery={(q) => update({ q: q || null })} chips={[]} active={!!params.get("status")} onRemove={() => {}}
        onClear={() => { const keep = new URLSearchParams(); for (const k of ["size", "sort"]) if (params.get(k)) keep.set(k, params.get(k)!); setParams(keep, { replace: true }); }}>
        <label htmlFor="g-status" className="visually-hidden">Status</label>
        <select id="g-status" className="report-select" value={params.get("status") ?? ""} onChange={(e) => update({ status: e.target.value })}>
          <option value="">Active and disabled</option>
          <option value="active">Active</option>
          <option value="disabled">Disabled</option>
        </select>
      </ListBar>
      <div id="groups-top">{groups.data.total === 0 ? <p className="small muted list-count" role="status">No group matches.</p> : pager("top")}</div>
      <div className="table-wrap">
        <table>
          <thead><tr>
            <SortHeading label="Group" sortKey="name" sort={sort} onSort={(v) => update({ sort: v === "name" ? null : v }, true)} />
            <th scope="col">Email domains</th><th scope="col">Services on</th><th scope="col">Pages</th></tr></thead>
          <tbody>
            {list.map((g) => {
              const on = g.services.filter((x) => x.enabled);
              const empty = on.filter((x) => x.metered && x.people_with_access > 0 && x.balance.remaining_pages <= 0);
              return (
                <tr key={g.id}>
                  <td className={g.parent ? "subgroup-cell" : undefined}>
                    {g.parent && <span className="subgroup-mark" aria-hidden="true">↳ </span>}
                    <Link to={`/admin/groups/${g.id}`}>{g.name}</Link>{g.status === "disabled" && <> <span className="pill muted">Disabled</span></>}
                    {g.parent && <div className="muted small">Subgroup of {g.parent.name}</div>}
                    {g.pending_members > 0 && <> <Link className="pill warn" to={`/admin/groups/${g.id}/people`}>{g.pending_members} waiting</Link></>}</td>
                  <td className="small">{g.domains.join(", ") || <span className="muted">none</span>}</td>
                  <td className="small">{on.map((x) => x.label).join(", ") || <span className="muted">none</span>}</td>
                  <td>{empty.length > 0
                    ? <span className="pill bad">{empty.map((x) => x.label).join(", ")}: no pages</span>
                    : <span className="muted small">{fmtNum(g.balance.remaining_pages)} Standard pages left{g.balance.shared ? ` (from ${g.parent?.name ?? "its parent"})` : ""}</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pager("bottom")}
      {can(me, "groups.manage") && (
        <p><DialogButton label="Create a group" title="Create a group" formId="new-group">
          {() => <NewGroup onCreated={(g) => navigate(`/admin/groups/${g.id}`, { state: SAVED })} />}
        </DialogButton></p>
      )}
    </>
  );
}

/** A new group, in the admin edit dialog (#511): opens once it's made. */
function NewGroup({ onCreated }: { onCreated: (g: Group) => void }) {
  const [name, setName] = useState("");
  const [domains, setDomains] = useState("");
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(!!(name.trim() || domains.trim()));

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onCreated(await post<Group>("/admin/groups", { name, domains: domains.split(/[\s,]+/).filter(Boolean) }));
    } catch (err) {
      setError(err);
    }
  }
  return (
    <form id="new-group" onSubmit={create} className="dialog-form">
      <div className="field"><label htmlFor="g-name">Name</label>
        <input id="g-name" type="text" required value={name} onChange={(e) => setName(e.target.value)} /></div>
      <div className="field"><label htmlFor="g-domains">Email domains <span className="hint">(comma-separated, e.g. example.org)</span></label>
        <input id="g-domains" type="text" value={domains} onChange={(e) => setDomains(e.target.value)} /></div>
      <div className="actions"><button className="btn" type="submit" disabled={!name.trim()}>Create group</button></div>
      <ErrorNotice error={error} />
    </form>
  );
}

/** Whoever opens a subgroup also runs its parent: super admins and service admins, and the parent's admins (#431). */
function runsParent(me: ReturnType<typeof useAuth>["me"], g: Group) {
  return !g.parent || acrossGroups(me) || !!me?.admin_of.includes(g.parent.id);
}

/** A group and its subgroups (#431), as far as this admin runs them: to switch between, or move people between. */
function familyOf(me: ReturnType<typeof useAuth>["me"], g: Group): { id: string; name: string; sub: boolean }[] {
  if (g.parent) {
    return runsParent(me, g) ? [{ ...g.parent, sub: false }, { id: g.id, name: g.name, sub: true }] : [{ id: g.id, name: g.name, sub: true }];
  }
  return [{ id: g.id, name: g.name, sub: false }, ...(g.subgroups ?? []).map((x) => ({ ...x, sub: true }))];
}

/** Switch between a group and its subgroups, keeping the tab; and, on a parent, add a subgroup. */
function FamilyPicker({ group }: { group: Group }) {
  const { me } = useAuth();
  const navigate = useNavigate();
  const params = useParams();
  const family = familyOf(me, group);
  const tab = params["*"] ? `/${params["*"]}` : "";
  return (
    <div className="family-picker">
      {family.length > 1 && (
        <div className="field">
          <label htmlFor="family-pick">Group or subgroup</label>
          <select id="family-pick" value={group.id} onChange={(e) => navigate(`/admin/groups/${e.target.value}${tab}`)}>
            {family.map((x) => <option key={x.id} value={x.id}>{x.sub && family[0].sub === false ? `– ${x.name}` : x.name}</option>)}
          </select>
        </div>
      )}
      {!group.parent && (
        <DialogButton label="Add a subgroup" title={`Add a subgroup to ${group.name}`} formId="new-subgroup">
          {() => <NewSubgroup parent={group} onCreated={(g) => navigate(`/admin/groups/${g.id}`, { state: SAVED })} />}
        </DialogButton>
      )}
    </div>
  );
}

/** A subgroup (#431): its people use the parent's pages and follow its settings, until it sets its own. */
function NewSubgroup({ parent, onCreated }: { parent: Group; onCreated: (g: Group) => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(!!name.trim());
  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onCreated(await post<Group>(`/groups/${parent.id}/subgroups`, { name }));
    } catch (err) {
      setError(err);
    }
  }
  return (
    <form id="new-subgroup" onSubmit={create} className="dialog-form">
      <p className="muted small">A subgroup uses {parent.name}'s pages (you can give it a limit), starts with its settings and
        services, and is managed by {parent.name}'s admins as well as any admins of its own. People join it by an address or
        email domain listed on it, or when an admin moves them there.</p>
      <div className="field"><label htmlFor="sg-name">Name</label>
        <input id="sg-name" type="text" required value={name} onChange={(e) => setName(e.target.value)} /></div>
      <div className="actions"><button className="btn" type="submit" disabled={!name.trim()}>Add subgroup</button></div>
      <ErrorNotice error={error} />
    </form>
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
  return (
    <>
      {acrossGroups(me) && <p className="small"><Link to="/admin/groups">← All groups</Link></p>}
      <div className="page-head">
        <div>
          <h1>{g.name}</h1>
          <p className="sub">
            {g.parent && <>Subgroup of {runsParent(me, g) ? <Link to={`/admin/groups/${g.parent.id}`}>{g.parent.name}</Link> : g.parent.name} · </>}
            {g.domains.join(", ") || "No email domains"} · local-only {g.local_only ? "on" : "off"}
            {g.status === "disabled" && " · disabled"}
          </p>
        </div>
        <FamilyPicker group={g} />
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
        <Route path="services" element={<ServicesAndPages group={g} addsPages={can(me, "pages")} onChange={(x) => group.setData(x)} reload={group.reload} />} />
        <Route path="people" element={<People group={g} onChange={() => void group.reload()} />} />
        <Route path="help" element={<GroupHelp group={g} />} />
        <Route path="documents" element={<Documents group={g} />} />
        <Route path="activity" element={<Navigate to={`${base}/reports?report=audit`} replace />} />
        <Route path="reports" element={<LiveReports groupId={g.id} />} />
        <Route path="settings" element={<GroupSettingsTab group={g} onChange={(x) => group.setData(x)} reload={group.reload} />} />
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
            <span className="lbl">{x.label} pages left {pagesOf(x.balance, group)}</span>
            <Meter {...meterOf(x.balance)} label={`${x.label} pool`} />
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

/** Where a group's pages come from: its pool, or (a subgroup, #431) its parent's, within its own limit. */
function pagesOf(b: Balance, g: Group) {
  if (!b.shared) return `of ${fmtNum(b.pool_pages)}`;
  const from = `from ${g.parent?.name ?? "its parent"}'s pool`;
  return b.cap_pages === null || b.cap_pages === undefined ? from : `of this subgroup's limit of ${fmtNum(b.cap_pages)}, ${from}`;
}

/** A meter for a group's pages: its pool, or a subgroup's limit (with none, the parent's pool as it stands). */
function meterOf(b: Balance) {
  if (b.shared && b.cap_pages !== null && b.cap_pages !== undefined) return { used: b.used_pages + b.reserved_pages, total: b.cap_pages };
  if (b.shared) return { used: Math.max(0, b.pool_pages - b.remaining_pages), total: b.pool_pages };
  return { used: b.used_pages + b.reserved_pages, total: b.pool_pages };
}

function forGroup(x: GroupService, g: Group) {
  if (x.key === "standard") return <span className="pill good">On</span>;
  if (!x.globally_enabled) return <span className="pill muted">Switched off for everyone</span>;
  if (x.external && g.local_only) return <span className="pill muted">Blocked: local-only</span>;
  if (g.parent && !x.available && x.enabled) return <span className="pill warn">Off at {g.parent.name}</span>;
  return x.enabled ? <span className="pill good">On{x.is_default ? " (preselected)" : ""}</span> : <span className="pill muted">Off</span>;
}

/** One table for every service the group could use: who has it, the group's pages, the default limit, the exceptions. */
function ServicesAndPages({ group, addsPages, onChange, reload }: {
  group: Group; addsPages: boolean; onChange: (g: Group) => void; reload: () => void;
}) {
  const { config, me } = useAuth();
  // Edit and Add pages open the admin edit dialog (#511), which asks before closing over unsaved changes
  const [open, setOpen] = useState<{ key: string; what: "edit" | "pages" | "limit" } | null>(null);
  const sub = !!group.parent;
  const setsLimit = sub && runsParent(me, group);
  return (
    <>
      {sub ? (
        <p className="muted">
          Every service the subgroup can use. Its pages are {group.parent!.name}'s: the subgroup has no pool of its own, and
          uses what {group.parent!.name} has left, up to a limit {setsLimit ? "you can set" : `${group.parent!.name}'s admins can set`}.
          A service needs to be on for {group.parent!.name} too.
        </p>
      ) : (
        <p className="muted">
          Every service the group can use, in one place. A person needs the service on for the group, to have it, and pages
          in the group's pool: a personal limit can't add pages the pool hasn't got.
        </p>
      )}
      <div className="table-wrap">
        <table className="svc-table">
          <thead>
            <tr><th scope="col">Service</th><th scope="col">For the group</th><th scope="col">Who has it</th>
              <th scope="col">{sub ? "Pages" : "Group's pages"}</th><th scope="col">Default per person</th><th scope="col">Own limits</th>
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
                        <div>{fmtNum(x.balance.remaining_pages)} left {pagesOf(x.balance, group)}</div>
                        <Meter {...meterOf(x.balance)} label={`${x.label} pool`} />
                        {empty && <div className="small bad-text">Empty: everyone sees 0 left, whatever their limit</div>}
                      </>
                    )}
                  </td>
                  <td>{!x.metered || !x.enabled ? "–" : x.default_user_cap === null ? "No limit" : fmtNum(x.default_user_cap)}</td>
                  <td>{x.own_limits > 0
                    ? <Link to={`/admin/groups/${group.id}/people?service=${encodeURIComponent(x.key)}`}>{x.own_limits} {x.own_limits === 1 ? "person" : "people"}</Link>
                    : <span className="muted">none</span>}</td>
                  <td>
                    <div className="row-actions">
                      <button className="btn secondary small" aria-haspopup="dialog" onClick={() => setOpen({ key: x.key, what: "edit" })}>
                        Edit<span className="visually-hidden"> {x.label}</span>
                      </button>
                      {x.metered && x.enabled && setsLimit && (
                        <button className="btn secondary small" aria-haspopup="dialog" onClick={() => setOpen({ key: x.key, what: "limit" })}>
                          Set limit<span className="visually-hidden"> for {x.label}</span>
                        </button>
                      )}
                      {x.metered && x.enabled && !sub && (
                        <button className={`btn small${empty ? "" : " secondary"}`} aria-haspopup="dialog"
                          onClick={() => setOpen({ key: x.key, what: "pages" })}>
                          {addsPages ? "Add pages" : "Request pages"}<span className="visually-hidden"> for {x.label}</span>
                        </button>
                      )}
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
        const close = () => setOpen(null);
        if (open.what === "edit") return (
          <AdminDialog title={`${x.label} for ${group.name}`} formId="sfg-form" onClose={close}>
            <ServiceForGroup key={x.key} group={group} svc={x} onSaved={(g) => { onChange(g); close(); }} onCancel={close} />
          </AdminDialog>
        );
        if (open.what === "limit") return (
          <AdminDialog title={`${x.label} limit for ${group.name}`} formId="cap-form" onClose={close}>
            <SubgroupLimit group={group} svc={x} onSaved={(g) => { onChange(g); close(); }} />
          </AdminDialog>
        );
        if (addsPages) return (
          <AdminDialog title={`Add ${x.label} pages to ${group.name}`} formId="topup-form" onClose={close} wide>
            <Topup base={`/admin/groups/${group.id}`} ledger={`/groups/${group.id}/ledger?kind=topup&limit=20`} fixedService={x.key}
              onDone={reload} formId="topup-form" bare />
          </AdminDialog>
        );
        return (
          <AdminDialog title={`More ${x.label} pages for ${group.name}`} onClose={close}>
            <p>Super admins and service admins add pages to a group's pool, each under an approving ticket.</p>
            {config.supportUrl
              ? <p><a className="btn" href={config.supportUrl} target="_blank" rel="noreferrer noopener">Request pages<span className="visually-hidden"> (opens in a new tab)</span></a></p>
              : <p className="muted">Ask the people who run this service{config.contactEmail ? <> at <a href={`mailto:${config.contactEmail}`}>{config.contactEmail}</a></> : ""}.</p>}
            <p className="small muted">Say the group ({group.name}), the service ({x.label}) and how many pages.</p>
          </AdminDialog>
        );
      })()}
    </>
  );
}

/** A subgroup's limit on its parent's pages for one service (#431), for those who run the parent. */
function SubgroupLimit({ group, svc, onSaved }: { group: Group; svc: GroupService; onSaved: (g: Group) => void }) {
  const was = svc.balance.cap_pages ?? null;
  const [cap, setCap] = useState(was === null ? "" : String(was));
  const [error, setError] = useState<unknown>(null);
  const dirty = cap !== (was === null ? "" : String(was));
  useUnsavedChanges(dirty);
  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onSaved(await patch<Group>(`/groups/${group.id}/services/${encodeURIComponent(svc.key)}`, { cap_pages: cap === "" ? null : Number(cap) }));
    } catch (err) {
      setError(err);
    }
  }
  return (
    <form id="cap-form" className="dialog-form" onSubmit={save}>
      <p className="muted small">The most {group.name} may use of {group.parent?.name}'s {svc.label} pages, counting what it has
        used ({fmtNum(svc.balance.used_pages)}) and what its documents hold now ({fmtNum(svc.balance.reserved_pages)}). Lowering it
        below that stops new documents; those already sent carry on.</p>
      <div className="field" style={{ maxWidth: 280 }}>
        <label htmlFor="cap-pages">Limit <span className="hint">(pages; empty = whatever {group.parent?.name} has left)</span></label>
        <input id="cap-pages" type="number" min={0} value={cap} onChange={(e) => setCap(e.target.value)} />
      </div>
      <div className="actions"><button className="btn" type="submit" disabled={!dirty}>Save</button></div>
      <ErrorNotice error={error} />
    </form>
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
    <form id="sfg-form" className="dialog-form" onSubmit={save}>
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

const PEOPLE_STATUSES: [string, string][] = [["", "Everyone"], ["active", "Active"], ["disabled", "Disabled"],
  ["declined", "Declined"], ["admin", "The group's admins"]];
const HAS: [string, string][] = [["yes", "Has"], ["no", "Doesn't have"]];

/** The group's people, with each one's access and limit for one service at a time: the only place limits are edited. */
function People({ group, onChange }: { group: Group; onChange: () => void }) {
  const { me, reloadMe } = useAuth();
  const [params, setParams] = useSearchParams();
  const enabled = group.services.filter((x) => x.enabled);
  const key = params.get("service") ?? enabled[0]?.key ?? "standard";
  const svc = group.services.find((x) => x.key === key) ?? enabled[0];
  // The view lives in the address (#514): search, status, who has the service, sort, page; people waiting for
  // approval are listed on their own, above
  const sort = params.get("sort") ?? "email";
  const page = Number(params.get("page")) || 1;
  const size = Number(params.get("size")) || 25;
  const api = new URLSearchParams();
  if (params.get("q")) api.set("q", params.get("q")!);
  if (params.get("status")) api.set("status", params.get("status")!);
  if (params.get("has")) { api.set("service", key); api.set("has", params.get("has") === "yes" ? "true" : "false"); }
  api.set("sort", sort);
  api.set("page", String(page));
  api.set("page_size", String(size));
  const users = useLoad(() => get<Paged<User>>(`/groups/${group.id}/people?${api}`), [group.id, api.toString()]);
  const pending = useLoad(() => get<User[]>(`/groups/${group.id}/users?approval=pending&limit=1000`), [group.id]);
  const update = (changes: Record<string, string | null>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v === null || v === "") next.delete(k); else next.set(k, v); }
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  };
  const replaceUser = (updated: User) => {
    if (users.data) users.setData({ ...users.data, items: users.data.items.map((x) => (x.id === updated.id ? updated : x)) });
  };
  const findUser = (id: string | null) => users.data?.items.find((x) => x.id === id) ?? pending.data?.find((x) => x.id === id);
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
      replaceUser(updated);
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
      replaceUser(updated);
      setSaved(`Describe with AI ${on === null ? "follows the group" : on ? "is on" : "is off"} for ${u.email}.`);
    } catch (e) {
      setError(e);
    }
  }

  async function status(u: User) {
    setError(null);
    try {
      const updated = await patch<User>(`/groups/${group.id}/users/${u.id}`, { status: u.status === "active" ? "disabled" : "active" });
      replaceUser(updated);
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
      replaceUser(updated);
      await Promise.all([users.reload(), pending.reload()]);  // they move from the waiting list into the list
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
    setOpen(u.id);
    void check(u);
  }

  /** Move someone between the group and its subgroups (#431). */
  async function move(u: User, to: string) {
    setError(null);
    try {
      const updated = await patch<User>(`/groups/${group.id}/users/${u.id}`, { group_id: to });
      replaceUser(updated);
      setSaved(`${u.email} moved to ${updated.group_name ?? "the group"}.`);
      onChange();
      if (!family.some((x) => x.id === updated.group_id) || (group.parent && updated.group_id !== group.id)) {
        setOpen(null);
        await users.reload();
      } else {
        await check(updated);  // their access now follows the group they're in
      }
    } catch (e) {
      setError(e);
    }
  }
  const family = familyOf(me, group);
  const several = family.length > 1;

  if (!svc) return <p className="muted">No services are on for this group yet.</p>;
  const waiting = pending.data ?? [];
  const list = users.data?.items ?? [];
  const label = (l: [string, string][], v: string | null) => l.find(([k]) => k === v)?.[1] ?? v ?? "";
  const chips: Chip[] = params.get("has") ? [{ key: "has", text: `${label(HAS, params.get("has"))} ${svc?.label ?? ""}` }] : [];
  const pager = (where: "top" | "bottom") => users.data && (
    <ListPager page={page} pageSize={users.data.page_size} total={users.data.total} noun={["person", "people"]} where={where}
      topId="gp-top" onPage={(n) => update({ page: n > 1 ? String(n) : null }, true)}
      onSize={(n) => update({ size: n === 25 ? null : String(n) })} />
  );
  const def = svc.default_user_cap === null ? "No limit" : `Default ${fmtNum(svc.default_user_cap)}`;
  return (
    <>
    {waiting.length > 0 && <WaitingList people={waiting} onDecide={decide} />}
    <section className="panel" aria-labelledby="people-h">
      <h2 id="people-h">People</h2>
      <div className="field" style={{ maxWidth: 320 }}><label htmlFor="p-svc">Show access and limits for</label>
        <select id="p-svc" value={svc.key} onChange={(e) => {
          update({ service: e.target.value, has: null });
          const u = findUser(open);
          if (u) void check(u, e.target.value);
        }}>
          {enabled.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select></div>
      <ListBar label="Search and filter the group's people" searchId="p-q" query={params.get("q") ?? ""} placeholder="Search by email or name"
        onQuery={(q) => update({ q: q || null })} chips={chips} active={!!params.get("status")}
        onRemove={(k) => update({ [k]: null })}
        onClear={() => { const keep = new URLSearchParams(); for (const k of ["service", "size", "sort"]) if (params.get(k)) keep.set(k, params.get(k)!); setParams(keep, { replace: true }); }}
        more={<div className="field"><label htmlFor="p-has">Has {svc.label}</label>
          <select id="p-has" value={params.get("has") ?? ""} onChange={(e) => update({ has: e.target.value })}>
            <option value="">Either</option>
            {HAS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>}>
        <label htmlFor="p-status" className="visually-hidden">Status</label>
        <select id="p-status" className="report-select" value={params.get("status") ?? ""} onChange={(e) => update({ status: e.target.value })}>
          {PEOPLE_STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </ListBar>
      <p className="muted small">
        {svc.label}: {svc.all_users ? "everyone in the group has it" : "only the people ticked below have it"}.
        {svc.metered ? ` An empty limit means the group default (${svc.default_user_cap === null ? "no limit" : fmtNum(svc.default_user_cap)} pages).` : " Use is counted, never limited."}
      </p>
      <ErrorNotice error={error} />
      <div className="visually-hidden" role="status" aria-live="polite">{saved}</div>
      {users.loading && !users.data ? <Loading /> : users.data && (
        <>
        <div id="gp-top">{users.data.total === 0 ? <p className="small muted list-count" role="status">Nobody matches.</p> : pager("top")}</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><SortHeading label="Person" sortKey="email" sort={sort} onSort={(v) => update({ sort: v === "email" ? null : v }, true)} />
                <th scope="col">Has it</th>
                {svc.metered && <th scope="col">Limit</th>}<th scope="col" className="num">Used</th>
                {svc.metered && <th scope="col" className="num">Left</th>}
                <th scope="col"><span className="visually-hidden">Manage</span></th></tr>
            </thead>
            <tbody>
              {list.map((u) => {
                const us = u.services.find((y) => y.key === svc.key);
                const admin = u.roles.some((r) => r.role === "group_admin" && r.group_id === (u.group_id ?? group.id));
                return (
                  <Fragment key={u.id}>
                    <tr>
                      <td>
                        <div>{u.email}{u.status !== "active" && <> <span className="pill muted">Disabled</span></>}
                          {u.approval === "declined" && <> <span className="pill warn">Declined</span></>}
                          {admin && <> <span className="pill info">Group admin</span></>}
                          {roleLabel(u.roles) && <> <span className="pill info">{roleLabel(u.roles)}</span></>}</div>
                        {several && u.group_id !== group.id && <div className="small">In {u.group_name}</div>}
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
                        <button className="btn secondary small" aria-haspopup="dialog" onClick={() => manage(u)}>
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
    </section>
    {/* One person's access check and account actions, in the admin edit dialog (#511): each takes effect at once */}
    {(() => {
      const u = findUser(open);
      if (!u) return null;
      return (
        <AdminDialog title={`Manage ${u.email}`} onClose={() => setOpen(null)}>
          <ErrorNotice error={error} />
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
            {u.id !== me?.id && several && !u.roles.some((r) => r.role !== "group_admin") && (
              <div className="person-section">
                <h3>Group</h3>
                <div className="field">
                  <label htmlFor={`mv-${u.id}`} className="small">{u.email} is in</label>
                  <select id={`mv-${u.id}`} value={u.group_id ?? ""} onChange={(e) => void move(u, e.target.value)}>
                    {family.map((x) => <option key={x.id} value={x.id}>{x.name}{x.sub ? " (subgroup)" : ""}</option>)}
                  </select>
                  <span className="hint">Moving them takes effect at once. Their documents and past use stay where they were;
                    if they're an admin of their group, they stop being one.</span>
                </div>
              </div>
            )}
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
        </AdminDialog>
      );
    })()}
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
  const removes = can(me, "documents.rerun_remove");
  return (
    <section className="panel" aria-labelledby="gjobs-h">
      <h2 id="gjobs-h">Documents</h2>
      <p className="muted small">Opening someone else's files is recorded in the audit log (Reports).
        {removes && " Select documents to remove their files now or delete them."}</p>
      <DocumentsList key={group.id} scope={{
        endpoint: `/groups/${group.id}/documents`,
        services: group.services.filter((s) => s.enabled || s.balance.used_pages > 0).map((s) => ({ key: s.key, label: s.label })),
        backLabel: `${group.name}: documents`,
        ...(removes ? { can: canAdminRemove,
          bar: (selected: Set<string>, onDone: (message: string) => void, extra: ReactNode) =>
            <AdminRemoveBar selected={selected} onDone={onDone} extra={extra} /> } : {}),
      }} />
    </section>
  );
}

function GroupSettingsTab({ group, onChange, reload }: { group: Group; onChange: (g: Group) => void; reload: () => void }) {
  const { me } = useAuth();
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
  async function apiTokens(on: boolean) {
    setError(null);
    try {
      onChange(await patch<Group>(`/groups/${group.id}`, { api_tokens_enabled: on }));
      setMsg(on ? "People in the group can now make API tokens." : "API tokens are off for the group; the ones already made stop working.");
    } catch (e) {
      setError(e);
    }
  }
  async function pdfua(change: { pdfua_choice?: boolean; pdfua_default?: PdfUa }) {
    setError(null);
    try {
      onChange(await patch<Group>(`/groups/${group.id}`, change));
      setMsg(change.pdfua_choice === false ? "People in the group get PDF/UA-1 only."
        : change.pdfua_choice ? "People in the group can now choose PDF/UA-2 or Both, where the service offers it."
          : `${PDFUA_LABELS[change.pdfua_default!]} is now preselected.`);
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
      {group.parent && <FromParent group={group} onChange={onChange} />}
      <section className="panel" aria-labelledby="approval-h">
        <h2 id="approval-h">New members</h2>
        <Follows group={group} setting="require_approval" />
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
          <input type="checkbox" checked={group.local_only} disabled={!!group.parent?.local_only && group.local_only}
            onChange={(e) => void localOnly(e.target.checked)} />
          <span><strong>Local only.</strong> Keep this group's documents on this service's own servers: services that send
            documents elsewhere can't be used, whatever else is switched on.
            {group.parent?.local_only && <span className="muted small"> {group.parent.name} is local-only, so its subgroups are too.</span>}
            {!group.parent && !!group.subgroups?.length && <span className="muted small"> Turning it on turns it on for every
              subgroup; turning it off turns it off for those that follow this group.</span>}</span>
        </label>
        <Follows group={group} setting="local_only" />
        <label className="check">
          <input type="checkbox" checked={group.show_external} onChange={(e) => void showExternal(e.target.checked)} />
          <span><strong>Show which options are external.</strong> Mark the processing options and AI levels that send
            documents to an outside provider on the upload form.</span>
        </label>
        <Follows group={group} setting="show_external" />
        <label className="check">
          <input type="checkbox" checked={group.api_tokens_enabled} onChange={(e) => void apiTokens(e.target.checked)} />
          <span><strong>Let people make API tokens.</strong> For scripts that send documents (and, if they choose, fetch
            the results), each token tied to one processing option and AI level. Super admins switch tokens on for the
            service first. Switching this off stops the group's tokens at once.</span>
        </label>
        {group.api_tokens_enabled && (
          <details className="group-tokens">
            <summary>The group's API tokens</summary>
            <GroupApiTokens groupId={group.id} />
          </details>
        )}
        <label className="check">
          <input type="checkbox" checked={!!group.pdfua_choice} onChange={(e) => void pdfua({ pdfua_choice: e.target.checked })} />
          <span><strong>Let people choose PDF/UA-2.</strong> On the upload form, people can then have a document delivered
            as PDF 2.0 (PDF/UA-2), or as a PDF/UA-1 file and a PDF/UA-2 copy (Both). Super admins offer PDF/UA-2 for the
            service first.</span>
        </label>
        {group.pdfua_choice && (
          <div className="field">
            <label htmlFor="g-pdfua">Preselected standard</label>
            <select id="g-pdfua" value={group.pdfua_default ?? "1"}
              onChange={(e) => void pdfua({ pdfua_default: e.target.value as PdfUa })}>
              {(["1", "2", "both"] as PdfUa[]).map((p) => <option key={p} value={p}>{PDFUA_LABELS[p]}</option>)}
            </select>
          </div>
        )}
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
        <Follows group={group} setting="ai_describe" />
        <ErrorNotice error={error} />
        <div role="status" className="small muted">{msg}</div>
      </section>
      {group.parent && (
        <p className="muted small">{group.parent.name}'s admins manage this subgroup too, as well as any admins listed for it below.</p>
      )}
      {can(me, "groups.manage") ? (
        <>
          <GroupSettings group={group} onSaved={onChange} canDisable={can(me, "groups.disable")} />
          <Domains group={group} onChange={onChange} />
          <GroupEmails group={group} onChange={onChange} />
          {can(me, "group_admins") && <GroupAdmins group={group} />}
          {can(me, "pages") && !group.parent && <Adjust base={`/admin/groups/${group.id}`} onDone={reload} services={group.services.map((x) => ({ key: x.key, label: x.label }))} />}
        </>
      ) : (
        <Notice kind="info">The group's name, email domains, admins and pages are managed by super admins and service admins.</Notice>
      )}
    </>
  );
}


// A subgroup's settings that follow its parent's (#431), by the name the API gives them
const SETTING_LABELS: Record<string, string> = {
  ai: "AI for uploads", local_only: "Local only", require_approval: "New members need approval",
  ai_describe: "Describe with AI", show_external: "Show which options are external", over_quota_message: "Over-quota message",
};

/** Under a setting on a subgroup's page: whether it follows the parent or is the subgroup's own. */
function Follows({ group, setting }: { group: Group; setting: string }) {
  if (!group.parent) return null;
  const follows = group.inherits?.includes(setting);
  return <p className="small muted follows">{follows ? `Follows ${group.parent.name}. Changing it here makes it this subgroup's own.`
    : `Set for this subgroup. ${group.parent.name}'s changes don't reach it (see Settings from ${group.parent.name}, above).`}</p>;
}

/** A subgroup's settings: which follow its parent, and a way back to the parent's for those it set itself. */
function FromParent({ group, onChange }: { group: Group; onChange: (g: Group) => void }) {
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const parent = group.parent!;
  const own = Object.keys(SETTING_LABELS).filter((k) => !group.inherits?.includes(k));
  async function follow(keys: string[]) {
    setError(null);
    try {
      onChange(await patch<Group>(`/groups/${group.id}`, { inherit: keys }));
      setMsg(`${keys.map((k) => SETTING_LABELS[k]).join(", ")} now ${keys.length === 1 ? "follows" : "follow"} ${parent.name}.`);
    } catch (e) {
      setError(e);
    }
  }
  return (
    <section className="panel" aria-labelledby="from-parent-h">
      <h2 id="from-parent-h">Settings from {parent.name}</h2>
      <p className="muted small">A subgroup follows {parent.name}'s settings until it sets its own. Its services, pages and email
        domains are set separately.</p>
      {own.length === 0 ? <p>Every setting below follows {parent.name}.</p> : (
        <ul className="plain-list">
          {own.map((k) => (
            <li key={k}><strong>{SETTING_LABELS[k]}</strong>: set for this subgroup{" "}
              <button type="button" className="btn secondary small" onClick={() => void follow([k])}>
                Use {parent.name}'s<span className="visually-hidden"> {SETTING_LABELS[k]}</span></button></li>
          ))}
        </ul>
      )}
      {own.length > 1 && <p><button type="button" className="btn secondary small" onClick={() => void follow(own)}>Follow {parent.name} for all of them</button></p>}
      <ErrorNotice error={error} />
      <div role="status" className="small muted">{msg}</div>
    </section>
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
      <Follows group={group} setting="ai" />
      {group.ai_mode === "fixed" && group.ai_level && !levels.some((x) => x.key === group.ai_level) && (
        <Notice kind="warn" title="The group's AI level isn't on here">
          <p>Everyone's documents use {group.services.find((x) => x.key === group.ai_level)?.label ?? group.ai_level}, which isn't
            switched on for {group.name}{group.parent ? ` (or ${group.parent.name})` : ""}, so uploads that need it are refused.
            Switch it on in <Link to={`/admin/groups/${group.id}/services`}>Services and pages</Link>, or choose another level here.</p>
        </Notice>
      )}
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
