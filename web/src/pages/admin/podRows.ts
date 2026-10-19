/** The pods view (#398): what a row and a box say. Kept apart from the page so it can be tested without a browser. */
const num = (n: number) => n.toLocaleString();

export type PodState = "working" | "idle" | "ready" | "starting" | "not_ready" | "failing" | "stopping";
export type Pod = {
  name: string; state: PodState; ready: boolean; phase: string | null; node: string | null; age_s: number | null;
  restarts: number; waiting_reason: string | null; last_restart_reason: string | null;
  liveness_failures_1h: number; readiness_failures_1h: number;
  cpu_cores: number | null; cpu_limit: number | null; memory_bytes: number | null; memory_limit: number | null;
  busy: number | null; places: number | null;
  namespace: string | null; containers: { name: string; image: string }[];
};
/** What a workload is and where it comes from (portal/workload_catalog.py, #419). */
export type About = {
  description: string; sources: { label: string; url: string }[]; port: number | null; shell: boolean;
};
export type Workload = {
  name: string; kind: string; min: number; max: number; desired: number; autoscaled: boolean;
  state: "ok" | "scaling" | "waiting"; queue: string | null; waiting: number | null; oldest_s: number | null;
  scaler: "keda" | "hpa" | null; about: About | null; pods: Pod[];
};
export type Workloads = { monitoring: boolean; reachable: boolean; error: string | null; workloads: Workload[] };

/** Each state's words and the mark inside its box, so color is never the only sign. */
export const POD_STATES: Record<PodState, { label: string; mark: string }> = {
  working: { label: "Working", mark: "▶" },
  idle: { label: "Idle", mark: "–" },
  ready: { label: "Ready", mark: "✓" },
  starting: { label: "Starting", mark: "…" },
  not_ready: { label: "Not ready", mark: "?" },
  failing: { label: "Failing", mark: "!" },
  stopping: { label: "Stopping", mark: "×" },
};

export function age(s: number | null) {
  if (s === null) return "–";
  if (s < 120) return `${Math.round(s)} s`;
  if (s < 7200) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

/** "1.2 of 3 cores", "2.1 GB of 6 GB". */
export function use(v: number | null, limit: number | null, fmt: (n: number) => string) {
  if (v === null) return "–";
  return limit ? `${fmt(v)} of ${fmt(limit)}` : fmt(v);
}
export const cores = (n: number) => `${Number.isInteger(n) ? n : n < 0.1 ? n.toFixed(2) : n.toFixed(1)} cores`;
/** Memory as Kubernetes counts it: 1 GiB = 1024 MiB. */
export const memory = (n: number) =>
  n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GiB` : `${Math.round(n / 2 ** 20)} MiB`;

/** What one pod is doing, in words: the box's label and the details' heading. */
export function podSummary(p: Pod) {
  const parts = [POD_STATES[p.state].label];
  if (p.places) parts.push(`${num(p.busy ?? 0)} of ${num(p.places)} documents`);
  if (p.waiting_reason) parts.push(p.waiting_reason);
  if (p.restarts) parts.push(`${p.restarts} restart${p.restarts === 1 ? "" : "s"}`);
  return parts.join(", ");
}

/** Boxes from 0 to the most it can have: its pods first, then outlines for the room it has to scale. */
export function slots(w: Workload): (Pod | null)[] {
  // Alike together, in the legend's order: working first, stopping last
  const order = Object.keys(POD_STATES);
  const pods = [...w.pods].sort((a, b) =>
    order.indexOf(a.state) - order.indexOf(b.state) || a.name.localeCompare(b.name));
  const room = Math.max(0, w.max - pods.filter((p) => p.state !== "stopping").length);
  return [...pods, ...Array<null>(room).fill(null)];
}

export function rowNote(w: Workload) {
  const bits = [];
  bits.push(w.autoscaled ? `${w.min} to ${w.max} pods, ${w.desired} wanted`
    : `${w.desired} pod${w.desired === 1 ? "" : "s"}`);
  if (w.queue) bits.push(`${num(w.waiting ?? 0)} waiting${w.oldest_s ? `, oldest ${age(w.oldest_s)}` : ""}`);
  if (w.state === "waiting") bits.push("at its maximum with work waiting");
  else if (w.state === "scaling") bits.push("starting pods");
  return bits.join(" · ");
}
