import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useParams } from "react-router-dom";
import { get, type Group, type Service } from "../../api";
import { useAuth } from "../../auth";
import { AccessCheckForm, AttentionList, serviceOptions } from "../../components/attention";
import { LiveReports } from "../../components/LiveReport";
import { CsvExports } from "../../components/tables";
import { ErrorNotice, Loading, useLoad, useTitle } from "../../components/ui";
import { acrossGroups, can } from "../../permissions";
import { GroupsList, GroupWorkspace } from "./Groups";
import { HelpDetail, HelpQueue } from "./Help";
import { PeopleAll } from "./People";
import { QueuesPage } from "./Queues";
import { ServiceDetail, ServicesList } from "./Services";
import { SettingsArea } from "./Settings";
import { SystemPage } from "./System";
import { OfferService } from "./Setup";
import { GatewayLink } from "./shared";

/** One administration area for super admins, service admins (#564) and group admins; what each sees follows their
 * role's permissions (src/permissions.ts). */
export function Admin() {
  const { me } = useAuth();
  const isSuper = !!me?.is_super_admin;
  const across = acrossGroups(me);
  // The menu can be folded away on any admin page; the choice is remembered on this device
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("admin-menu") === "collapsed"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("admin-menu", collapsed ? "collapsed" : "open"); } catch { /* not stored: fine */ }
  }, [collapsed]);
  const waiting = (me?.pending_approvals ?? 0) + (can(me, "help") ? me?.help_needs_us ?? 0 : 0);
  return (
    <div className={`admin-shell${collapsed ? " collapsed" : ""}`}>
      <nav className="admin-side" aria-label="Administration">
        <button type="button" className="menu-toggle" aria-expanded={!collapsed} aria-controls="admin-links"
          onClick={() => setCollapsed(!collapsed)} title={collapsed ? "Show the menu" : "Hide the menu for more room"}>
          {collapsed ? <><span aria-hidden="true">☰</span><span className="visually-hidden">Show the administration menu</span>
            {waiting > 0 && <span className="badge">{waiting}<span className="visually-hidden"> waiting</span></span>}</>
            : <><span aria-hidden="true">«</span> Hide menu</>}
        </button>
        <div id="admin-links" className="admin-links" hidden={collapsed}>
          <NavLink to="/admin" end>Attention</NavLink>
          <NavLink to="/admin/groups">{across ? "Groups" : me && me.admin_of.length === 1 ? "My group" : "My groups"}
            {!!me?.pending_approvals && <> <span className="badge">{me.pending_approvals}<span className="visually-hidden"> waiting for approval</span></span></>}</NavLink>
          {can(me, "help") && (
            <NavLink to="/admin/help">Help requests
              {!!me?.help_needs_us && <> <span className="badge">{me.help_needs_us}<span className="visually-hidden"> waiting on us</span></span></>}</NavLink>
          )}
          {isSuper && <NavLink to="/admin/services">Services and AI levels</NavLink>}
          {can(me, "people") && <NavLink to="/admin/people">People (all groups)</NavLink>}
          {can(me, "queues") && <NavLink to="/admin/queues">Queues</NavLink>}
          {can(me, "system") && <NavLink to="/admin/system">System</NavLink>}
          {can(me, "reports.all") && <NavLink to="/admin/reports">Reports</NavLink>}
          {isSuper && <NavLink to="/admin/settings">Settings</NavLink>}
          <hr />
          <NavLink to="/admin/offer">+ Offer a service to a group</NavLink>
          {isSuper && <span className="small"><GatewayLink /></span>}
        </div>
      </nav>
      <div className="admin-main">
        <Routes>
          <Route index element={<AdminHome />} />
          <Route path="groups" element={<GroupsList />} />
          <Route path="groups/:groupId/*" element={<GroupWorkspace />} />
          <Route path="offer" element={<OfferService />} />
          <Route path="help/:id" element={<HelpDetail />} />
          {can(me, "help") && <Route path="help" element={<HelpQueue />} />}
          {isSuper && <Route path="services" element={<ServicesList />} />}
          {isSuper && <Route path="services/:key" element={<ServiceDetail />} />}
          {can(me, "people") && <Route path="people" element={<PeopleAll />} />}
          {can(me, "people") && <Route path="users" element={<Navigate to="/admin/people" replace />} />}
          {can(me, "queues") && <Route path="queues" element={<QueuesPage />} />}
          {can(me, "system") && <Route path="system/*" element={<SystemPage />} />}
          {can(me, "reports.all") && (
            <>
              <Route path="reports" element={<><h1>Reports</h1><LiveReports />
                <details className="panel">
                  <summary>Fixed-format CSV exports</summary>
                  <p className="muted small">The original exports, unchanged for scripts and finance. Everything above can also be exported as shown.</p>
                  <CsvExports base="/admin/reports" reports={[
                    { name: "groups", label: "groups summary", hint: "Each group's pool, use, top-ups and overrides." },
                    { name: "usage", label: "usage by person (all groups)", hint: "Everyone's limits and use." },
                    { name: "ledger", label: "page ledger (all groups)", hint: "Every top-up, override, use and refund." },
                    { name: "access", label: "sign-in log (all groups)", hint: "Sign-ins, failed attempts and sign-outs." },
                  ]} />
                </details></>} />
              <Route path="audit" element={<Navigate to="/admin/reports?report=audit" replace />} />
            </>
          )}
          {isSuper && <Route path="settings/*" element={<SettingsArea />} />}
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </div>
    </div>
  );
}

/** Attention first. Super admins and service admins see everything; group admins see their groups. */
function AdminHome() {
  useTitle("Administration");
  const { me } = useAuth();
  const all = can(me, "attention");
  const services = useLoad(() => (all ? get<Service[]>("/admin/services") : Promise.resolve([] as Service[])), [all]);
  const groups = useLoad(() => (all ? Promise.resolve([] as Group[]) : get<Group[]>("/groups")), [all]);
  if (all) {
    return (
      <>
        <h1>Needs attention</h1>
        <AttentionList path="/admin/attention" title="Across every group and service" />
        {services.data && <AccessCheckForm path="/admin/access-check" services={serviceOptions(services.data)} />}
      </>
    );
  }
  if (groups.loading && !groups.data) return <Loading />;
  if (!groups.data) return <ErrorNotice error={groups.error} />;
  return (
    <>
      <h1>Needs attention</h1>
      {groups.data.map((g) => (
        <div key={g.id} className="admin-main">
          <AttentionList path={`/groups/${g.id}/attention`} title={g.name} />
          <AccessCheckForm path={`/groups/${g.id}/access-check`} services={serviceOptions(g.services.filter((x) => x.enabled))} />
        </div>
      ))}
    </>
  );
}

/** Old /manage links (bookmarks, emails) go to the same place in the new area. */
export function ManageRedirect() {
  const params = useParams();
  const rest = params["*"] ?? "";
  const tab = { users: "people", jobs: "documents", audit: "reports?report=audit" }[rest] ?? rest;
  return <Navigate to={params.groupId ? `/admin/groups/${params.groupId}${tab ? `/${tab}` : ""}` : "/admin/groups"} replace />;
}
