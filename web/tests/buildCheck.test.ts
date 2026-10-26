// After a deploy, the next move to another page loads the new build: never with unsaved changes, never in a loop,
// one fetch for clicks close together. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { SHARE_MS, decide, isMove, servedBuild, sharedFetch } from "../src/buildCheck";
import { RELOAD_GUARD_MS } from "../src/staleBuild";

function memory() {
  const kept = new Map<string, string>();
  return { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v) };
}

test("build.json's id is read, and anything else says nothing", () => {
  assert.equal(servedBuild({ build: "9cb3250abc" }), "9cb3250abc");
  for (const body of [null, {}, { build: "" }, { build: 3 }, "<!doctype html>"]) assert.equal(servedBuild(body), null);
});

test("a changed id reloads; the same id, or none, doesn't", () => {
  assert.equal(decide("old", "new", false, memory(), 1_000_000), "reload");
  assert.equal(decide("old", "old", false, memory(), 1_000_000), "stay");
  assert.equal(decide("old", null, false, memory(), 1_000_000), "stay"); // offline, a 404 during a deploy
});

test("unsaved changes wait for a later move", () => {
  const store = memory();
  assert.equal(decide("old", "new", true, store, 1_000_000), "later");
  assert.equal(decide("old", "new", false, store, 1_000_000 + 1_000), "reload"); // saved now: the next move reloads
});

test("a new page that still reports another id doesn't reload again within a minute", () => {
  const store = memory();
  assert.equal(decide("old", "new", false, store, 1_000_000), "reload");
  assert.equal(decide("mixed", "new", false, store, 1_000_000 + 5_000), "stay");
  assert.equal(decide("mixed", "new", false, store, 1_000_000 + RELOAD_GUARD_MS + 1), "reload");
  assert.equal(decide("old", "new", false, null, 1), "stay"); // nowhere to note the reload
});

test("clicks close together share one fetch, and a failed fetch says nothing", async () => {
  let t = 0, calls = 0;
  const check = sharedFetch(async () => { calls++; return { build: `b${calls}` }; }, () => t);
  assert.equal(await check(), "b1");
  t = SHARE_MS - 1;
  assert.equal(await check(), "b1");
  assert.equal(calls, 1);
  t = SHARE_MS;
  assert.equal(await check(), "b2");
  const failing = sharedFetch(() => Promise.reject(new TypeError("Failed to fetch")), () => 0);
  assert.equal(await failing(), null);
});

test("a move is another path or tab, not a filter or sort", () => {
  assert.ok(isMove({ pathname: "/", search: "" }, { pathname: "/jobs/1", search: "" }));
  assert.ok(isMove({ pathname: "/jobs/1", search: "?tab=review" }, { pathname: "/jobs/1", search: "?tab=report" }));
  assert.ok(!isMove({ pathname: "/jobs/1", search: "?tab=review" }, { pathname: "/jobs/1", search: "?tab=review&q=x" }));
  assert.ok(!isMove({ pathname: "/", search: "" }, { pathname: "/", search: "?sort=name" }));
});
