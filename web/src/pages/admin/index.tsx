import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useParams } from "react-router-dom";
import { get, type Group, type Service } from "../../api";
import { useAuth } from "../../auth";
import { AccessCheckForm, AttentionList, serviceOptions } from "../../components/attention";
import { LiveReports } from "../../components/LiveReport";
import { CsvExports } from "../../components/tables";
import { ErrorNotice, Loading, useLoad, useTitle } from "../../components/ui";
import { GroupsList, GroupWorkspace } from "./Groups";
import { HelpDetail, HelpQueue } from "./Help";
import { PeopleAll } from "./People";
import { QueuesPage } from "./Queues";
import { ServiceDetail, ServicesList } from "./Services";
import { SettingsArea } from "./Settings";
import { SystemPage } from "./System";
import { OfferService } from "./Setup";
import { GatewayLink } from "./shared";

/** One administration area for super admins and group admins; what each sees follows their role. */
export function Admin() {
  const { me } = useAuth();
  const isSuper = !!me?.is_super_admin;
  // The menu can be folded away on any admin page; the choice is remembered on this device
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("admin-menu") === "collapsed"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("admin-menu", collapsed ? "collapsed" : "open"); } catch { /* not stored: fine */ }
  }, [collapsed]);
  const waiting = (me?.pending_approvals ?? 0) + (isSuper ? me?.help_needs_us ?? 0 : 0);
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
          <NavLink to="/admin/groups">{isSuper ? "Groups" : me && me.admin_of.length === 1 ? "My group" : "My groups"}
            {!!me?.pending_approvals && <> <span className="badge">{me.pending_approvals}<span className="visually-hidden"> waiting for approval</span></span></>}</NavLink>
          {isSuper && (
            <>
              <NavLink to="/admin/help">Help requests
                {!!me?.help_needs_us && <> <span className="badge">{me.help_needs_us}<span className="visually-hidden"> waiting on us</span></span></>}</NavLink>
              <NavLink to="/admin/services">Services and AI levels</NavLink>
              <NavLink to="/admin/people">People (all groups)</NavLink>
              <NavLink to="/admin/queues">Queues</NavLink>
              <NavLink to="/admin/system">System</NavLink>
              <NavLink to="/admin/reports">Reports</NavLink>
              <NavLink to="/admin/settings">Settings</NavLink>
            </>
          )}
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
          {isSuper && (
            <>
              <Route path="help" element={<HelpQueue />} />
              <Route path="services" element={<ServicesList />} />
              <Route path="services/:key" element={<ServiceDetail />} />
              <Route path="people" element={<PeopleAll />} />
              <Route path="queues" element={<QueuesPage />} />
              <Route path="system" element={<SystemPage />} />
              <Route path="users" element={<Navigate to="/admin/people" replace />} />
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
              <Route path="settings/*" element={<SettingsArea />} />
              <Route path="audit" element={<Navigate to="/admin/reports?report=audit" replace />} />
            </>
          )}
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </div>
    </div>
  );
}

/** Attention first. Super admins see everything; group admins see their groups. */
function AdminHome() {
  useTitle("Administration");
  const { me } = useAuth();
  const isSuper = !!me?.is_super_admin;
  const services = useLoad(() => (isSuper ? get<Service[]>("/admin/services") : Promise.resolve([] as Service[])), [isSuper]);
  const groups = useLoad(() => (isSuper ? Promise.resolve([] as Group[]) : get<Group[]>("/groups")), [isSuper]);
  if (isSuper) {
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
