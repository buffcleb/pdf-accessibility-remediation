import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get, post } from "../../api";
import { ReportChart, type ChartData } from "../../components/ReportChart";
import { PodView } from "./Pods";
import { ErrorNotice, Loading, Notice, errorMessage, fmtNum, useLoad, useTitle } from "../../components/ui";

type Figure = { key: string; label: string; value: number | null; unit: string };
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
  return f.unit === "s" ? duration(f.value) : fmtNum(Math.round(f.value));
}

/** Super admins (#397): the service's health from its Prometheus, now and over time, and the way into Grafana. */
export function SystemPage() {
  useTitle("System");
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
      {!d.monitoring ? (
        <Notice kind="info" title="Monitoring isn't part of this deployment">With MONITORING=on in deploy/k8s/deploy.env, the
          next deploy adds Prometheus and Grafana, and this page shows their figures.</Notice>
      ) : (
        <>
          <div className="system-actions">
            {d.grafana && (
              <button type="button" className="btn" onClick={openGrafana} disabled={opening}>
                {opening ? "Opening…" : "Open Grafana"}<span className="visually-hidden"> (opens in a new tab)</span>
              </button>
            )}
            <nav className="report-pills" aria-label="Charts: how far back">
              {RANGES.map(([key, label]) => (
                <button key={key} type="button" className="pill-link" aria-pressed={range === key}
                  onClick={() => setRange(key)}>{label}</button>
              ))}
            </nav>
          </div>
          {openError && <Notice kind="bad" title="Grafana didn't open">{openError}</Notice>}
          {!d.reachable ? (
            <Notice kind="bad" title="Prometheus isn't answering">{d.error ?? "No answer."} Super admins are told by email
              if this lasts; the portal's own alerts still work.</Notice>
          ) : (
            <>
              <h2>Now</h2>
              <dl className="grid system-figures">
                {d.now.map((f) => (
                  <div key={f.key} className={`stat${f.key === "alerts" && (f.value ?? 0) > 0 ? " stat-bad" : ""}`}>
                    <dt className="lbl">{f.label}</dt><dd className="num">{show(f)}</dd>
                  </div>
                ))}
              </dl>
              <PodView />
              <h2>Over time</h2>
              <div className="system-charts">
                {d.charts.map((c) => c.chart.points.length > 0 ? (
                  <ReportChart key={c.key} chart={c.chart} period="time" title={c.title} busy={data.loading}
                    note="" />
                ) : (
                  <p key={c.key} className="muted"><strong>{c.title}</strong>: nothing recorded yet.</p>
                ))}
              </div>
              <p className="muted small">Grafana has these and more, in detail; the charts show at most eight series each.</p>
            </>
          )}
        </>
      )}
    </>
  );
}
