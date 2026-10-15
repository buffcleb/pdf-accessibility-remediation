// Isolating a tag. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap } from "../src/api";
import { holes, isolateItem, subtree } from "../src/viewer/isolate";

const el = (i: number, parent: number | null, kids: number[], boxes: [number, [number, number, number, number]][]): TagElement =>
  ({ i, role: "P", parent, kids, own: true, order: null, boxes: boxes.map(([page, bbox]) => ({ page, bbox })) } as TagElement);

// Document > [L > [LI > LBody, LI > LBody], P]; the list runs onto page 2
const map: TagMap = { schema: 1, pages: [{ w: 600, h: 800 }, { w: 600, h: 800 }], elements: [
  el(0, null, [1, 6], []), el(1, 0, [2, 4], [[1, [50, 600, 500, 800]], [2, [50, 0, 500, 60]]]),
  el(2, 1, [3], []), el(3, 2, [], [[1, [70, 600, 500, 700]]]), el(4, 1, [5], []), el(5, 4, [], [[2, [70, 0, 500, 60]]]),
  el(6, 0, [], [[2, [50, 100, 500, 200]]]),
] };

test("an isolated tag keeps itself and everything inside it in view, on every page it's on", () => {
  assert.deepEqual([...subtree(map, 1)].sort(), [1, 2, 3, 4, 5]);
  assert.deepEqual(holes(map, subtree(map, 1), 1), [[50, 600, 500, 800], [70, 600, 500, 700]]);
  assert.deepEqual(holes(map, subtree(map, 1), 2), [[50, 0, 500, 60], [70, 0, 500, 60]]);
  assert.deepEqual(holes(map, subtree(map, 4), 1), []);          // nothing of it on page 1: all of it veiled
});

test("the menu isolates a tag, or ends the isolation", () => {
  assert.deepEqual([isolateItem(null, 3).act, isolateItem(null, 3).disabled], ["isolate", undefined]);
  assert.equal(isolateItem(3, 5).act, "unisolate");
  assert.ok(isolateItem(null, null).disabled);
});
