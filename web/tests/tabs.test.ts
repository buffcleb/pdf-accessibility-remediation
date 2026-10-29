// Tabs: the keys that move between them, and the tab the Areas window opens on. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { tabStep } from "../src/components/tabKeys";
import { areaTab } from "../src/viewer/areaTabs";

test("arrow keys move to the next and previous tab, going round at the ends", () => {
  assert.equal(tabStep("ArrowRight", 0, 3), 1);
  assert.equal(tabStep("ArrowRight", 2, 3), 0);
  assert.equal(tabStep("ArrowLeft", 0, 3), 2);
  assert.equal(tabStep("ArrowLeft", 2, 3), 1);
});

test("Home and End go to the first and last tab; other keys do nothing", () => {
  assert.equal(tabStep("Home", 2, 3), 0);
  assert.equal(tabStep("End", 0, 3), 2);
  assert.equal(tabStep("Enter", 1, 3), null);
  assert.equal(tabStep("ArrowDown", 1, 3), null);
  assert.equal(tabStep("ArrowRight", 0, 0), null);
});

test("an area opens on its tagged content, or on New tag when only untagged content is in it, never on the AI", () => {
  assert.equal(areaTab(3, 0), "tagged");
  assert.equal(areaTab(3, 2), "tagged");
  assert.equal(areaTab(0, 0), "tagged");
  assert.equal(areaTab(0, 2), "new");
});
