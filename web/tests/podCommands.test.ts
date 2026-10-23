// The pod details' kubectl commands (#419): only those that apply, with the pod's real name and namespace.
// Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { mainContainer, podCommands } from "../src/pages/admin/podCommands";
import type { About, Container, Pod, Workload } from "../src/pages/admin/podRows";

/** Only the name matters to the commands. */
const c = (name: string, image: string): Container => ({
  name, image, product: "", release: null, source: null, digest: null, pinned: null, matches: null,
});

const pod = (more: Partial<Pod> = {}): Pod => ({
  name: "engine-worker-7f-x2", state: "working", ready: true, phase: "Running", node: "n1", age_s: 60, restarts: 0,
  waiting_reason: null, last_restart_reason: null, liveness_failures_1h: 0, readiness_failures_1h: 0,
  cpu_cores: null, cpu_limit: null, memory_bytes: null, memory_limit: null, busy: null, places: null,
  namespace: "pdf-remediation", containers: [c("engine-worker", "x/engine:1")], ...more,
});
const about = (more: Partial<About> = {}): About => ({ description: "d.", sources: [], port: null, shell: true,
  ...more });
const workload = (more: Partial<Workload> = {}): Workload => ({
  name: "engine-worker", kind: "deployment", min: 1, max: 4, desired: 2, autoscaled: true, state: "ok",
  queue: "engine", waiting: 0, oldest_s: null, scaler: "keda", about: about(), pods: [], ...more,
});
const all = (w: Workload, p: Pod) => podCommands(w, p).flatMap((g) => g.commands.map((c) => c.command));

test("commands use the pod's name and namespace, and a KEDA-scaled one gets its scaler, not scale", () => {
  const cmds = all(workload(), pod());
  assert.ok(cmds.includes("kubectl -n pdf-remediation logs engine-worker-7f-x2"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation get scaledobject engine-worker"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation rollout restart deployment/engine-worker"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation exec -it engine-worker-7f-x2 -- sh"));
  assert.ok(!cmds.some((c) => c.includes(" scale ")));
  assert.ok(!cmds.some((c) => c.includes("--previous")));  // it hasn't restarted
  assert.ok(!cmds.some((c) => c.includes("port-forward")));  // nothing worth reaching
});

test("a fixed-size stateful set with a port: scale, port-forward, and --previous after a restart", () => {
  const w = workload({ name: "postgres", kind: "statefulset", scaler: null, autoscaled: false, desired: 1,
    about: about({ port: 5432 }) });
  const p = pod({ name: "postgres-0", restarts: 2, containers: [c("postgres", "postgres:16")] });
  const cmds = all(w, p);
  assert.ok(cmds.includes("kubectl -n pdf-remediation scale statefulset/postgres --replicas=1"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation port-forward pod/postgres-0 5432:5432"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation logs --previous postgres-0"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation rollout status statefulset/postgres"));
});

test("several containers: every one's log, and -c picks the one the workload is named after", () => {
  const p = pod({ containers: [c("a-sidecar", "s"), c("engine-worker", "e")] });
  assert.equal(mainContainer(workload(), p), "engine-worker");
  const cmds = all(workload(), p);
  assert.ok(cmds.includes("kubectl -n pdf-remediation logs engine-worker-7f-x2 --all-containers"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation logs engine-worker-7f-x2 -c a-sidecar"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation logs -f engine-worker-7f-x2 -c engine-worker"));
  assert.ok(cmds.includes("kubectl -n pdf-remediation exec -it engine-worker-7f-x2 -c engine-worker -- sh"));
});

test("no shell, no exec; an unknown workload gets neither exec nor port-forward; changes are grouped apart", () => {
  assert.ok(!all(workload({ about: about({ shell: false }) }), pod()).some((c) => c.includes(" exec ")));
  const unknown = all(workload({ about: null, scaler: "hpa" }), pod({ namespace: null }));
  assert.ok(!unknown.some((c) => c.includes(" exec ") || c.includes("port-forward")));
  assert.ok(unknown.includes("kubectl -n NAMESPACE get hpa"));
  const groups = podCommands(workload(), pod());
  assert.deepEqual(groups.map((g) => g.changes), [false, false, true]);
  assert.ok(groups[2].commands.every((c) => / (rollout restart|delete|scale|exec) /.test(c.command)));
  const readOnly = groups.slice(0, 2).flatMap((g) => g.commands);
  assert.ok(readOnly.every((c) => !/ (rollout restart|delete|exec) /.test(c.command)));
});
