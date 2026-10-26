// Check only per AI level (#586). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkNotOffered, checkNotOfferedLine } from "../src/components/checkOffer";

const levels = [
  { key: "local", label: "Local AI", audit_ai: "free" },
  { key: "comm", label: "Commercial AI", audit_ai: "disabled" },
  { key: "cloud", label: "Cloud AI", audit_ai: "disabled" },
  { key: "off", label: "Basic", audit_ai: "off" },
];

test("a level with checks disabled is named; others aren't", () => {
  assert.deepEqual(checkNotOffered(levels, ["local"]), []);
  assert.deepEqual(checkNotOffered(levels, ["off"]), []);
  assert.deepEqual(checkNotOffered(levels, ["comm"]), ["Commercial AI"]);
  assert.deepEqual(checkNotOffered(levels, [null, undefined]), []);
});

test("a file's own level counts too, each named once", () => {
  assert.deepEqual(checkNotOffered(levels, ["local", "comm", "comm", "cloud"]), ["Commercial AI", "Cloud AI"]);
});

test("the line under the buttons", () => {
  assert.equal(checkNotOfferedLine(["Commercial AI"]),
    "Check only isn't offered with Commercial AI. To check, choose another AI level.");
  assert.equal(checkNotOfferedLine(["A", "B", "C"]),
    "Check only isn't offered with A, B and C. To check, choose another AI level.");
});
