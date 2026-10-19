// The pods view (#398): boxes, words and numbers. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { age, cores, memory, podSummary, rowNote, slots, type Pod, type Workload } from "../src/pages/admin/podRows";

const pod = (name: string, state: Pod["state"], more: Partial<Pod> = {}): Pod => ({
  name, state, ready: state !== "starting", phase: "Running", node: "k8s-01", age_s: 3600, restarts: 0,
  waiting_reason: null, last_restart_reason: null, liveness_failures_1h: 0, readiness_failures_1h: 0,
  cpu_cores: null, cpu_limit: null, memory_bytes: null, memory_limit: null, busy: null, places: null,
  namespace: "pdf", containers: [], ...more,
});
const workload = (pods: Pod[], more: Partial<Workload> = {}): Workload => ({
  name: "engine-worker", kind: "deployment", min: 1, max: 4, desired: pods.length, autoscaled: true, state: "ok",
  queue: "engine", waiting: 0, oldest_s: null, scaler: "keda", about: null, pods,
  ...more,
});

test("a row's boxes: its pods, alike together, then room up to its maximum", () => {
  const w = workload([pod("b", "idle"), pod("c", "stopping"), pod("a", "working"), pod("d", "starting")]);
  const s = slots(w);
  assert.deepEqual(s.map((p) => p?.name ?? null), ["a", "b", "d", "c", null]);  // a pod stopping doesn't take room
  assert.deepEqual(slots(workload([], { min: 0, max: 2 })), [null, null]);
});

test("what a pod and a row say", () => {
  assert.equal(podSummary(pod("a", "working", { busy: 1, places: 2, restarts: 3 })),
    "Working, 1 of 2 documents, 3 restarts");
  assert.equal(podSummary(pod("a", "failing", { waiting_reason: "CrashLoopBackOff", restarts: 1 })),
    "Failing, CrashLoopBackOff, 1 restart");
  assert.equal(rowNote(workload([pod("a", "working")], { waiting: 3, oldest_s: 1200, state: "waiting", max: 1 })),
    "1 to 1 pods, 1 wanted · 3 waiting, oldest 20 min · at its maximum with work waiting");
  assert.equal(rowNote(workload([pod("p", "ready")], { name: "postgres", autoscaled: false, queue: null })), "1 pod");
});

test("numbers as people read them", () => {
  assert.deepEqual([age(45), age(1200), age(10800), age(259200), age(null)], ["45 s", "20 min", "3 h", "3 d", "–"]);
  assert.deepEqual([cores(2), cores(0.75), cores(0.031)], ["2 cores", "0.8 cores", "0.03 cores"]);
  assert.deepEqual([memory(6 * 2 ** 30), memory(512 * 2 ** 20)], ["6.0 GiB", "512 MiB"]);
});
