// A page loaded before a deploy reloads once to fetch the new build, and never loops. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { RELOAD_GUARD_MS, isStaleBuildError, shouldReload } from "../src/staleBuild";

test("a failed load of part of the app is recognised in each browser's words", () => {
  for (const msg of ["Importing a module script failed.", "Failed to fetch dynamically imported module: /assets/x.js",
    "error loading dynamically imported module: https://pdf.example.edu/assets/TagViewer-abc.js",
    "Loading chunk 123 failed."]) {
    assert.ok(isStaleBuildError(new TypeError(msg)), msg);
  }
  assert.ok(!isStaleBuildError(new Error("Cannot read properties of undefined")));
  assert.ok(!isStaleBuildError(null));
});

test("it reloads once, then not again within a minute, and never without storage", () => {
  const kept = new Map<string, string>();
  const store = { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v) };
  assert.equal(shouldReload(store, 1_000_000), true);
  assert.equal(shouldReload(store, 1_000_000 + 5_000), false); // that reload didn't help: show the error page
  assert.equal(shouldReload(store, 1_000_000 + RELOAD_GUARD_MS + 1), true); // a later deploy can reload again
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => undefined };
  assert.equal(shouldReload(broken, 1), false);
  assert.equal(shouldReload(null, 1), false);
});
