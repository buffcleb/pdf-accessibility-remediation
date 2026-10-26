import { useState, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { get, post } from "../../api";
import { useAuth } from "../../auth";
import { can } from "../../permissions";
import { ReportChart, type ChartData } from "../../components/ReportChart";
import { PodView, VersionsView } from "./Pods";
import { ErrorNotice, Loading, Notice, errorMessage, fmtDate, fmtNum, useLoad, useTitle } from "../../components/ui";

type Figure = { key: string; label: string; value: number | null; unit: string; of?: number | null };
type System = {
  monitoring: boolean; reachable: boolean; grafana: boolean; error: string | null;
  now: Figure[]; charts: { key: string; title: string; chart: ChartData }[];
};
const RANGES = [["6h", "6 hours"], ["24h", "Day"], ["7d", "Week"]] as const;
const always = () => true;

/** "4 min", "2 h": how long the oldest document has waited. */
function duration(s: number) {
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

function show(f: Figure) {
  if (f.value === null) return "–";
  if (f.unit === "s") return duration(f.value);
  if (f.unit === "cores" || f.unit === "GiB") {
    // Use against capacity (#522): "7.2 of 24 cores"
    const n = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 1 });
    return <>{n(f.value)} <span className="system-of">{f.of ? `of ${n(f.of)} ` : ""}{f.unit}</span></>;
  }
  return fmtNum(Math.round(f.value));
}

/** The capacity figures (#522): use against what there is, by everything on the nodes and by this service. */
const CAPACITY = new Set(["env_cpu", "env_memory", "svc_cpu", "svc_memory"]);

type Siem = {
  mode: "off" | "syslog" | "https"; destination: string | null; pull: boolean; sent_up_to: string | null;
  last_success: string | null; last_error: string | null; last_error_at: string | null; failures: number;
  behind_seconds: number;
};

/** Where the audit log goes (#456): the SIEM export's place, and its last error. */
function SiemPanel() {
  const data = useLoad(() => get<Siem>("/admin/audit/siem"), [], always, 60000);
  const s = data.data;
  if (!s) return data.loading ? <Loading /> : <ErrorNotice error={data.error} />;
  if (s.mode === "off" && !s.pull) {
    return <p className="muted small">The audit log isn't sent to a SIEM (PORTAL_SIEM_MODE; docs/operations/siem.md).</p>;
  }
  return (
    <section aria-labelledby="siem-h">
      <h2 id="siem-h">Audit log to the SIEM</h2>
      <dl className="grid system-figures">
        <div className="stat"><dt className="lbl">Sent by</dt>
          <dd className="num">{s.mode === "off" ? "–" : s.mode === "syslog" ? "Syslog" : "HTTPS"}</dd></div>
        {s.destination && <div className="stat"><dt className="lbl">To</dt><dd className="num">{s.destination}</dd></div>}
        <div className="stat"><dt className="lbl">Sent up to</dt><dd className="num">{fmtDate(s.sent_up_to) || "–"}</dd></div>
        <div className={`stat${s.behind_seconds > 3600 ? " stat-bad" : ""}`}><dt className="lbl">Waiting for</dt>
          <dd className="num">{s.behind_seconds ? duration(s.behind_seconds) : "–"}</dd></div>
        <div className="stat"><dt className="lbl">Last sent</dt><dd className="num">{fmtDate(s.last_success) || "–"}</dd></div>
        {s.pull && <div className="stat"><dt className="lbl">Pull API</dt><dd className="num">On</dd></div>}
      </dl>
      {s.failures > 0 && s.last_error && (
        <Notice kind="bad" title={`Not sent: ${s.failures} tr${s.failures === 1 ? "y" : "ies"} in a row`}>
          {s.last_error} ({fmtDate(s.last_error_at)}). It's tried again, waiting longer each time; nothing is lost.
        </Notice>
      )}
    </section>
  );
}

/** Super admins and service admins (#397, #564): the service's health from its Prometheus, now and over time; and, for
 * super admins, the way into Grafana. */
export function SystemPage() {
  useTitle("System");
  const { me } = useAuth();
  const [params] = useSearchParams();
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("6h");
  const data = useLoad(() => get<System>(`/admin/monitoring/system?range=${range}`), [range], always, 60000);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  async function openGrafana() {
    setOpening(true);
    setOpenError(null);
    try {
      // The portal gives this browser Grafana's cookie (eight hours), then Grafana opens beside the app
      const { url } = await post<{ url: string }>("/admin/monitoring/grafana");
      const w = window.open(url, "_blank");
      if (w) w.opener = null;
      else window.location.assign(url);
    } catch (e) {
      setOpenError(errorMessage(e));
    } finally {
      setOpening(false);
    }
  }

  if (data.loading && !data.data) return <Loading />;
  if (!data.data) return <ErrorNotice error={data.error} />;
  const d = data.data;
  return (
    <>
      <h1>System</h1>
      <p className="muted">How the service is doing, from its monitoring (Prometheus): documents, the engine's workers, and
        what each service uses. Problems it finds are on Needs attention and emailed to super admins, with the portal's own.</p>
      {params.get("grafana") === "signin" && (
        <Notice kind="info" title="Open Grafana from here">Grafana is reached through this page: choose Open Grafana. Its
          sign-in lasts eight hours, and only while you're a super admin.</Notice>
      )}
      <div className="system-actions">
        <nav className="tabs" aria-label="System sections">
          <NavLink to="/admin/system" end>Now</NavLink>
          <NavLink to="/admin/system/over-time">Over time</NavLink>
          <NavLink to="/admin/system/versions">Versions</NavLink>
          <NavLink to="/admin/system/audit-export">Audit export</NavLink>
        </nav>
        {d.monitoring && d.grafana && can(me, "grafana") && (
          <button type="button" className="btn secondary small" onClick={openGrafana} disabled={opening}>
            {opening ? "Opening…" : "Open Grafana"}<span className="visually-hidden"> (opens in a new tab)</span>
          </button>
        )}
      </div>
      {openError && <Notice kind="bad" title="Grafana didn't open">{openError}</Notice>}
      <Routes>
        <Route index element={<Monitored d={d}>
          <h2>Documents and engine</h2>
          <dl className="grid system-figures">
            {d.now.filter((f) => !CAPACITY.has(f.key)).map((f) => (
              <div key={f.key} className={`stat${f.key === "alerts" && (f.value ?? 0) > 0 ? " stat-bad" : ""}`}>
                <dt className="lbl">{f.label}</dt><dd className="num">{show(f)}</dd>
              </div>
            ))}
          </dl>
          <h2>Capacity</h2>
          <p className="muted small">CPU and memory in use against what the nodes have: everything on them, and this
            service's share. By node and over time on <strong>Over time</strong>.</p>
          <dl className="grid system-figures">
            {d.now.filter((f) => CAPACITY.has(f.key)).map((f) => (
              <div key={f.key} className="stat">
                <dt className="lbl">{f.label}</dt><dd className="num">{show(f)}</dd>
              </div>
            ))}
          </dl>
          <PodView />
        </Monitored>} />
        <Route path="over-time" element={<Monitored d={d}>
          <nav className="report-pills" aria-label="Charts: how far back">
            {RANGES.map(([key, label]) => (
              <button key={key} type="button" className="pill-link" aria-pressed={range === key}
                onClick={() => setRange(key)}>{label}</button>
            ))}
          </nav>
          <div className="system-charts">
            {d.charts.map((c) => c.chart.points.length > 0 ? (
              <ReportChart key={c.key} chart={c.chart} period="time" title={c.title} busy={data.loading} note="" />
            ) : (
              <p key={c.key} className="muted"><strong>{c.title}</strong>: nothing recorded yet.</p>
            ))}
          </div>
          <p className="muted small">Grafana has these and more, in detail; the charts show at most eight series each.</p>
        </Monitored>} />
        <Route path="versions" element={<Monitored d={d}><VersionsView /></Monitored>} />
        <Route path="audit-export" element={<SiemPanel />} />
        <Route path="*" element={<Navigate to="/admin/system" replace />} />
      </Routes>
    </>
  );
}

/** What a tab shows when the deployment has no monitoring, or Prometheus doesn't answer; else its content. */
function Monitored({ d, children }: { d: System; children: ReactNode }) {
  if (!d.monitoring) {
    return (
      <Notice kind="info" title="Monitoring isn't part of this deployment">With MONITORING=on in deploy/k8s/deploy.env, the
        next deploy adds Prometheus and Grafana, and this page shows their figures.</Notice>
    );
  }
  if (!d.reachable) {
    return (
      <Notice kind="bad" title="Prometheus isn't answering">{d.error ?? "No answer."} Super admins are told by email
        if this lasts; the portal's own alerts still work.</Notice>
    );
  }
  return <>{children}</>;
}
