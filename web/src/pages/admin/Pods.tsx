import { useState } from "react";
import { get } from "../../api";
import { LiveRegion, Notice, fmtNum, useLoad } from "../../components/ui";
import { POD_STATES, age, cores, memory, pin, podSummary, release, rowNote, shortDigest, slots, use, versionRows,
  type Pod, type PodState, type Workload, type Workloads } from "./podRows";
import { podCommands } from "./podCommands";
import { copyText } from "../../components/copy";

const always = () => true;

/** Super admins (#398): every workload's pods, from Prometheus, refreshed every 15 seconds. */
export function PodView() {
  const data = useLoad(() => get<Workloads>("/admin/monitoring/workloads"), [], always, 15000);
  const [selected, setSelected] = useState<string | null>(null);
  if (!data.data) return null;
  const d = data.data;
  if (!d.monitoring) return null;
  if (!d.reachable) {
    return <Notice kind="bad" title="The pods can't be shown">{d.error ?? "Prometheus didn't answer."}</Notice>;
  }
  return (
    <section aria-labelledby="pods-heading">
      <h2 id="pods-heading">Pods</h2>
      <ul className="pod-legend" aria-label="What the boxes mean">
        {(Object.keys(POD_STATES) as PodState[]).map((s) => (
          <li key={s}>
            <span className={`pod-box ${s}`} aria-hidden="true">{POD_STATES[s].mark}</span>{POD_STATES[s].label}
          </li>
        ))}
        <li><span className="pod-box empty" aria-hidden="true" />Room to scale</li>
      </ul>
      <div className="pod-rows">
        {d.workloads.map((w) => (
          <div key={w.name} className={`pod-row ${w.state}`}>
            <div className="pod-name">
              <strong>{w.name}</strong>
              <span className="muted small">{rowNote(w)}</span>
            </div>
            <ul className="pod-slots" aria-label={`${w.name}: ${rowNote(w)}`}>
              {slots(w).map((p, i) => p ? (
                <li key={p.name}>
                  <button type="button" className={`pod-box ${p.state}`} aria-pressed={selected === p.name}
                    aria-label={`${p.name}: ${podSummary(p)}`} title={`${p.name}: ${podSummary(p)}`}
                    onClick={() => setSelected(selected === p.name ? null : p.name)}>
                    <span aria-hidden="true">{POD_STATES[p.state].mark}</span>
                  </button>
                </li>
              ) : (
                <li key={`room-${i}`}><span className={`pod-box empty${i < w.min ? " below-min" : ""}`} role="img"
                  aria-label={i < w.min ? "missing pod" : "room to scale"} /></li>
              ))}
            </ul>
            {w.pods.filter((p) => p.name === selected).map((p) => <PodDetail key={p.name} w={w} p={p} />)}
          </div>
        ))}
      </div>
      <Versions workloads={d.workloads} />
    </section>
  );
}

/** Every workload's release in one table, for checking against an advisory (#541). */
function Versions({ workloads }: { workloads: Workload[] }) {
  const rows = versionRows(workloads);
  if (rows.length === 0) return null;
  return (
    <>
      <h3 id="versions-heading">Versions</h3>
      <p className="muted small">The release each workload runs, from its image's tag (this service's own images:
        the commit they were built from), or as PostgreSQL and Redis report it. The full digest is in each pod's
        <strong> About</strong>.</p>
      <div className="table-wrap">
        <table aria-labelledby="versions-heading">
          <thead><tr>
            <th scope="col">Workload</th><th scope="col">Release</th><th scope="col">Digest</th>
            <th scope="col">Pinned</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.workload}/${r.container.name}/${r.container.digest}/${r.container.image}`}>
                <td>{r.workload}{r.container.name !== r.workload && <span className="muted"> ({r.container.name})</span>}
                  {rows.filter((x) => x.workload === r.workload && x.container.name === r.container.name).length > 1
                    && <span className="muted small"> {r.pods} pod{r.pods === 1 ? "" : "s"}</span>}</td>
                <td>{release(r.container)}</td>
                <td><code>{shortDigest(r.container.digest)}</code></td>
                <td>{r.container.matches === false ? <strong>{pin(r.container)}</strong> : pin(r.container)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** One pod's details, under its row. */
function PodDetail({ w, p }: { w: Workload; p: Pod }) {
  return (
    <div className="panel pod-detail" role="region" aria-label={`Pod ${p.name}`}>
      <h3>{p.name} <span className="muted small">({w.name})</span></h3>
      <About w={w} p={p} />
      <p>{podSummary(p)}</p>
      <dl className="pod-facts">
        <dt>Age</dt><dd>{age(p.age_s)}</dd>
        <dt>Node</dt><dd>{p.node ?? "–"}</dd>
        <dt>Ready</dt><dd>{p.ready ? "Yes" : "No"}{p.phase && p.phase !== "Running" ? ` (${p.phase})` : ""}</dd>
        <dt>Restarts</dt><dd>{p.restarts}{p.last_restart_reason ? `, last: ${p.last_restart_reason}` : ""}</dd>
        <dt>Probe failures, last hour</dt>
        <dd>liveness {p.liveness_failures_1h}, readiness {p.readiness_failures_1h}</dd>
        <dt>CPU</dt><dd>{use(p.cpu_cores, p.cpu_limit, cores)}</dd>
        <dt>Memory</dt><dd>{use(p.memory_bytes, p.memory_limit, memory)}</dd>
        {p.places !== null && <><dt>Documents</dt><dd>{fmtNum(p.busy ?? 0)} of {fmtNum(p.places)}</dd></>}
      </dl>
      <Commands w={w} p={p} />
    </div>
  );
}

/** What the workload is, where it comes from, and the images the pod runs (#419). */
function About({ w, p }: { w: Workload; p: Pod }) {
  const a = w.about;
  return (
    <details className="pod-about">
      <summary>About {w.name}</summary>
      {a ? <p>{a.description}</p> : <p className="muted">Not one of this service's own workloads: no description.</p>}
      {a && a.sources.length > 0 && (
        <>
          <h4>Where it comes from</h4>
          <ul>
            {a.sources.map((s) => (
              <li key={s.url}><a href={s.url} target="_blank" rel="noreferrer noopener">{s.label}</a></li>
            ))}
          </ul>
        </>
      )}
      {p.containers.length > 0 && (
        <>
          <h4>{p.containers.length === 1 ? "Image" : "Images"}</h4>
          <ul>
            {p.containers.map((c) => (
              <li key={c.name}>
                {p.containers.length > 1 && <>{c.name}: </>}<code>{c.image || "–"}</code>
                <dl className="pod-facts">
                  <dt>Version</dt><dd>{release(c)}</dd>
                  <dt>Digest</dt><dd>{c.digest ? <code className="pod-digest">{c.digest}</code> : "Not known yet"}</dd>
                  <dt>Pinned</dt><dd>{pin(c)}</dd>
                </dl>
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  );
}

/** The kubectl commands that apply to this pod, each with a copy button (#419). */
function Commands({ w, p }: { w: Workload; p: Pod }) {
  const [copied, setCopied] = useState("");
  const copy = async (command: string) => {
    setCopied(await copyText(command) ? `Copied: ${command}` : "Couldn't copy: select the command and copy it yourself.");
  };
  return (
    <details className="pod-commands">
      <summary>kubectl commands</summary>
      {podCommands(w, p).map((g) => (
        <section key={g.title} aria-label={g.title}>
          <h4>{g.title}</h4>
          {g.changes && <p className="muted small">Each of these changes something on the cluster.</p>}
          <ul>
            {g.commands.map((c) => (
              <li key={c.command}>
                <span className="muted small">{c.what}</span>
                <span className="pod-command">
                  <code>{c.command}</code>
                  <button type="button" className="btn secondary small" aria-label={`Copy: ${c.command}`}
                    onClick={() => void copy(c.command)}>Copy</button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <LiveRegion message={copied} />
    </details>
  );
}
