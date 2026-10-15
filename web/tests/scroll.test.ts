// Where the viewer scrolls to show a tag. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { centered, keptInView } from "../src/viewer/scroll";

test("a tag chosen elsewhere goes to the middle of the pane, as far as the top allows", () => {
  assert.equal(centered(1000, 20, 600), 710);      // 710 + 290 = 1000: the row's middle at the view's middle
  assert.equal(centered(100, 20, 600), 0);         // near the top: as close as it gets
});

test("a tag chosen in the same pane is only kept in view", () => {
  assert.equal(keptInView(300, 20, 200, 600), 200);     // already showing: no movement
  assert.equal(keptInView(100, 20, 200, 600), 100);     // above: to the top
  assert.equal(keptInView(900, 20, 200, 600), 320);     // below: just to the bottom
  assert.equal(keptInView(900, 800, 200, 600), 900);    // taller than the view: its top
});
