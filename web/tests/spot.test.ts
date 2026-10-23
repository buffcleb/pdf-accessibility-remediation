// Show for every review item with a page, and the area it's about (#426). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap } from "../src/api";
import { findItem, showUrl, spotExtent, spotFor, spotWords } from "../src/viewer/spot";

const MAP: TagMap = {
  schema: 1, pages: [{ w: 600, h: 800 }, { w: 600, h: 800 }],
  elements: [
    { i: 0, role: "Document", parent: null, kids: [1], boxes: [], own: false, order: null },
    { i: 1, role: "Figure", parent: 0, kids: [], own: true, order: null,
      boxes: [{ page: 1, bbox: [10, 10, 50, 50] }, { page: 2, bbox: [20, 20, 80, 80] }] },
  ] as TagElement[],
};

test("Show opens the tag manager at the item's page, its tag and its key", () => {
  assert.equal(showUrl("j1", { severity: "high", issue: "x", page: 3, element: 7, key: "abc" }),
    "/jobs/j1/tags?v=1&page=3&el=7&f=abc");
  assert.equal(showUrl("j1", { severity: "review", issue: "x", page: 2 }), "/jobs/j1/tags?v=1&page=2");
  assert.equal(showUrl("j1", { severity: "review", issue: "x", element: 4 }, 2), "/jobs/j1/tags?v=2&el=4");
  assert.equal(showUrl("j1", { severity: "review", issue: "about the whole document" }), null);
});

test("the area is the item's own boxes, else its tag's on its page, else the whole page", () => {
  const boxed = spotFor({ severity: "high", issue: "hidden", page: 1, boxes: [[1, 2, 3, 4], [5, 6, 7, 8]] }, MAP);
  assert.deepEqual(boxed, { page: 1, boxes: [[1, 2, 3, 4], [5, 6, 7, 8]], issue: "hidden" });
  assert.deepEqual(spotFor({ severity: "review", issue: "alt", page: 2, element: 1, key: "k" }, MAP),
    { page: 2, boxes: [[20, 20, 80, 80]], issue: "alt", key: "k" });
  assert.deepEqual(spotFor({ severity: "review", issue: "alt", element: 1 }, MAP)?.page, 1); // the tag's first page
  assert.deepEqual(spotFor({ severity: "review", issue: "right to left", page: 2 }, MAP),
    { page: 2, boxes: [], issue: "right to left" });
  assert.equal(spotFor({ severity: "review", issue: "x", page: 9 }, MAP), null); // no such page
  assert.equal(spotFor({ severity: "review", issue: "x" }, MAP), null);
});

test("the item a link names is found by key, else by page and tag", () => {
  const items = [{ severity: "high", issue: "a", page: 1, key: "k1" },
    { severity: "review", issue: "b", page: 2, element: 1 }, { severity: "review", issue: "c", page: 2 }];
  assert.equal(findItem(items, "k1", 5, null)?.issue, "a");
  assert.equal(findItem(items, "gone", 2, 1)?.issue, "b");
  assert.equal(findItem(items, null, 2, null)?.issue, "b");
  assert.equal(findItem(items, null, 3, null), null);
});

test("a spot's extent and its words", () => {
  const boxes: [number, number, number, number][] = [[10, 50, 20, 60], [5, 70, 30, 90]];
  const s = { page: 4, boxes, issue: "faint text" };
  assert.deepEqual(spotExtent(s), [5, 50, 30, 90]);
  assert.equal(spotExtent({ ...s, boxes: [] }), null);
  assert.equal(spotWords(s), "Showing: faint text, page 4.");
});
