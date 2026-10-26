// When a dropdown such as a report's Columns closes (#569). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { dropdownClose } from "../src/components/dropdownClose";

test("a click or tap outside closes it; inside, it stays open", () => {
  assert.equal(dropdownClose({ kind: "pointer", inside: false }), "close");
  assert.equal(dropdownClose({ kind: "pointer", inside: true }), "stay");
});

test("Escape closes it and puts the focus back on its button; other keys don't", () => {
  assert.equal(dropdownClose({ kind: "key", key: "Escape" }), "close-and-focus");
  for (const key of [" ", "Enter", "Tab", "ArrowDown"]) assert.equal(dropdownClose({ kind: "key", key }), "stay");
});

test("Tab out of it closes it; moving between its boxes, or clicking between them, doesn't", () => {
  assert.equal(dropdownClose({ kind: "focus", to: "outside" }), "close");
  assert.equal(dropdownClose({ kind: "focus", to: "inside" }), "stay");
  assert.equal(dropdownClose({ kind: "focus", to: null }), "stay");
});
