// Which tag a click on the page means. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap } from "../src/api";
import { chooseAt, tagsAt } from "../src/viewer/hit";

const el = (i: number, role: string, parent: number | null, bbox: [number, number, number, number] | null): TagElement =>
  ({ i, role, parent, kids: [], own: false, order: null, boxes: bbox ? [{ page: 1, bbox }] : [] } as TagElement);

// Document > Sect > L > LI > LBody, and a paragraph beside the list
const map: TagMap = { schema: 1, pages: [{ w: 600, h: 800 }], elements: [
  el(0, "Document", null, [0, 0, 600, 800]), el(1, "Sect", 0, [50, 50, 550, 400]), el(2, "L", 1, [60, 100, 540, 300]),
  el(3, "LI", 2, [60, 100, 540, 140]), el(4, "LBody", 3, [80, 100, 540, 140]), el(5, "P", 1, [60, 320, 540, 380]),
] };

test("the tags under a point, innermost first; the document's top tag and tags merged away don't count", () => {
  assert.deepEqual(tagsAt(map, 1, 100, 120), [4, 3, 2, 1]);
  assert.deepEqual(tagsAt(map, 1, 70, 120), [3, 2, 1]);           // beside the body, in the item
  assert.deepEqual(tagsAt(map, 1, 100, 120, new Set([4])), [3, 2, 1]);
  assert.deepEqual(tagsAt(map, 2, 100, 120), []);                 // another page
});

test("a click chooses the innermost tag, even when an outer one is selected; the same place again goes out a step", () => {
  const here = tagsAt(map, 1, 100, 120);
  assert.equal(chooseAt(here, 2, null), 4);                        // the list was selected: its item's body is clicked
  assert.equal(chooseAt(here, 4, { first: 4, chose: 4 }), 3);      // again: the item
  assert.equal(chooseAt(here, 3, { first: 4, chose: 3 }), 2);      // and again: the list
  assert.equal(chooseAt(here, 1, { first: 4, chose: 1 }), 4);      // after the outermost, back in
  assert.equal(chooseAt(here, 3, { first: 4, chose: 4 }), 4);      // the selection changed elsewhere: start inside
  assert.equal(chooseAt([], 3, null), null);
});
