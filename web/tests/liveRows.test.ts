// What a row of the live queue view says. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { howLong, liveQuery, span, stageLabel } from "../src/pages/admin/liveRows";

const T = Date.parse("2026-10-13T12:00:00Z");
const ago = (s: number) => new Date(T - s * 1000).toISOString();

test("a length of time keeps its seconds while it is short", () => {
  assert.deepEqual([0, 40_000, 59_400, 60_000, 12 * 60_000, 3_600_000, 3 * 3_600_000 + 5 * 60_000, -5].map(span),
    ["0 s", "40 s", "59 s", "1 min", "12 min", "1 h", "3 h 5 min", "0 s"]);
});

test("how long: running since it started, waiting since it was sent, and how long ago it ended", () => {
  assert.equal(howLong({ state: "active", created_at: ago(600), started_at: ago(95), finished_at: null }, T), "for 1 min");
  assert.equal(howLong({ state: "waiting", created_at: ago(40), started_at: null, finished_at: null }, T), "for 40 s");
  assert.equal(howLong({ state: "done", created_at: ago(900), started_at: ago(500), finished_at: ago(80) }, T), "1 min ago, took 7 min");
  assert.equal(howLong({ state: "failed", created_at: ago(900), started_at: null, finished_at: ago(30) }, T), "30 s ago");
  assert.equal(howLong({ state: "canceled", created_at: ago(900), started_at: null, finished_at: null }, T), "");
});

test("a stage is named in words, an unknown one as the engine names it, and only while in progress", () => {
  assert.equal(stageLabel({ state: "active", stage: "verifying_tags", kind: "documents" }), "AI: reviewing the tags");
  assert.equal(stageLabel({ state: "active", stage: "some_new_step", kind: "documents" }), "some new step");
  assert.equal(stageLabel({ state: "active", stage: null, kind: "edits" }), "Applying the edit");
  assert.equal(stageLabel({ state: "active", stage: null, kind: "reviews" }), "AI: reviewing the tags");
  assert.equal(stageLabel({ state: "waiting", stage: "tagging", kind: "documents" }), "");
});

test("the address's filters become the request's", () => {
  assert.equal(liveQuery(new URLSearchParams("view=live&q=budget&group=g1&in=mail&state=waiting")),
    "q=budget&group_id=g1&queue=mail&state=waiting");
  assert.equal(liveQuery(new URLSearchParams("view=live")), "");
});
