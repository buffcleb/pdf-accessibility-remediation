// A table cell's headers in the tag editor. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagEdit, TagElement, TagMap } from "../src/api";
import { headerChoices, headersOf, tableOf } from "../src/viewer/cells";
import { describe, editMarks } from "../src/viewer/draft";

type Spec = Partial<TagElement> & { i: number; role: string; parent: number | null };
const doc = (specs: Spec[]): TagMap => ({
  schema: 1, pages: [{ w: 600, h: 800 }],
  elements: specs.map((s) => ({ kids: specs.filter((k) => k.parent === s.i).map((k) => k.i), boxes: [], own: true, page: 1, order: null, ...s } as TagElement)),
});

// Table > TR [TH Region, TH 2025] > TR [TH North, TD 10 (named: 2025, North)]; and a table nested in the 10 cell
const sales = () => doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "Table", parent: 0 },
  { i: 2, role: "TR", parent: 1 },
  { i: 3, role: "TH", parent: 2, text: "Region" },
  { i: 4, role: "TH", parent: 2, text: "2025" },
  { i: 5, role: "TR", parent: 1 },
  { i: 6, role: "TH", parent: 5, text: "North" },
  { i: 7, role: "TD", parent: 5, text: "10", headers: [4, 6] },
  { i: 8, role: "Table", parent: 7 },
  { i: 9, role: "TR", parent: 8 },
  { i: 10, role: "TH", parent: 9, text: "Inner" },
  { i: 11, role: "P", parent: 0, text: "After" },
]);

test("a cell's choices are the header cells of its own table, in table order", () => {
  const map = sales();
  assert.equal(tableOf(map, 7), 1);
  assert.equal(tableOf(map, 10), 8);
  assert.equal(tableOf(map, 11), null);
  assert.deepEqual(headerChoices(map, [], 7).map((e) => e.i), [3, 4, 6]);   // not the nested table's header
  assert.deepEqual(headerChoices(map, [], 3).map((e) => e.i), [4, 6]);      // a header has headers too, not itself
  assert.deepEqual(headerChoices(map, [], 11), []);
  // A header made a data cell before saving is no longer offered; a data cell made a header is
  const ops: TagEdit[] = [{ op: "set_cell", element: 6, expect_role: "TH", role: "TD" }];
  assert.deepEqual(headerChoices(map, ops, 7).map((e) => e.i), [3, 4]);
});

test("a cell's headers come from the file until changed, and the change is described", () => {
  const map = sales();
  assert.deepEqual(headersOf(map, [], 7), [4, 6]);
  const ops: TagEdit[] = [{ op: "set_headers", element: 7, expect_role: "TD", headers: [6] }];
  assert.deepEqual(headersOf(map, ops, 7), [6]);
  assert.ok(/“10”.*read with “North”/.test(describe(ops[0], map)));
  assert.deepEqual(editMarks(map, ops).get(7), ["headers"]);
  assert.ok(/Headers removed/.test(describe({ op: "set_headers", element: 7, expect_role: "TD", headers: [] }, map)));
});
