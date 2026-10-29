// Removing or deleting tags, one or several chosen together. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap } from "../src/api";
import { deleteCheck, deleteMany } from "../src/viewer/deleting";
import { draftTree } from "../src/viewer/draft";
import { tagMenu } from "../src/viewer/menu";

type Spec = Partial<TagElement> & { i: number; role: string; parent: number | null };

function doc(specs: Spec[]): TagMap {
  let order = 0;
  const elements = specs.map((s): TagElement => ({
    kids: specs.filter((k) => k.parent === s.i).map((k) => k.i),
    boxes: s.own ? [{ page: 1, bbox: [0, 100 * s.i, 200, 100 * s.i + 20] }] : [],
    own: false, page: 1, ...s, order: s.own ? order++ : null,
  } as TagElement));
  return { schema: 1, pages: [{ w: 600, h: 800 }], elements };
}

// A heading, a section of two paragraphs (one with a link), a list of one item, and a lone paragraph
const MAP = doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "H1", parent: 0, own: true, text: "Title" },
  { i: 2, role: "Sect", parent: 0 },
  { i: 3, role: "P", parent: 2, own: true, text: "First" },
  { i: 4, role: "P", parent: 2, own: true, text: "See the site" },
  { i: 5, role: "Link", parent: 4, own: true, text: "the site", annot: true },
  { i: 6, role: "L", parent: 0 },
  { i: 7, role: "LI", parent: 6 },
  { i: 8, role: "LBody", parent: 7, own: true, text: "One" },
  { i: 9, role: "P", parent: 0, own: true, text: "Last" },
]);

test("one tag: what removing it or deleting it with everything in it would do", () => {
  const sect = deleteCheck(MAP, [], 2);
  assert.deepEqual([sect.kids, sect.going, sect.kept, sect.single], [[3, 4], [4, 3], [5], false]);
  assert.equal(sect.liftWhy + sect.deleteWhy, "");
  assert.ok(deleteCheck(MAP, [], 9).single);                        // nothing inside: the choices are the same
  assert.ok(/has to stay tagged/.test(deleteCheck(MAP, [], 5).deleteWhy));
  assert.ok(/can't move up/.test(deleteCheck(MAP, [], 7).liftWhy));    // a list item's body needs its item
  assert.ok(/top tag stays/.test(deleteCheck(MAP, [], 0).deleteWhy));
});

test("several tags: one choice for all of them; those that can't go are named and left", () => {
  const { ops, done, refused } = deleteMany(MAP, [], [1, 7, 9], "lift");
  assert.deepEqual(done, [1, 9]);
  assert.deepEqual(refused.map((r) => r.i), [7]);
  // With nothing inside, removing a tag is deleting it
  assert.deepEqual(ops, [
    { op: "delete", element: 1, expect_role: "H1", children: "delete" },
    { op: "delete", element: 9, expect_role: "P", children: "delete" }]);
  const all = deleteMany(MAP, [], [2, 6], "delete");
  assert.deepEqual(all.done, [2, 6]);
  const gone = draftTree(MAP, all.ops).deleted;
  assert.ok([2, 3, 4, 6, 7, 8].every((i) => gone.has(i)) && !gone.has(5));   // the link stays
});

test("a tag inside another deleted with everything goes with it; removed, each is checked after the one before", () => {
  const all = deleteMany(MAP, [], [2, 3], "delete");
  assert.deepEqual([all.done, all.within], [[2], [3]]);
  // Removing the section first puts its paragraphs in the document; the paragraph is then removed from there
  const lift = deleteMany(MAP, [], [2, 4], "lift");
  assert.deepEqual(lift.done, [2, 4]);
  assert.equal(draftTree(MAP, lift.ops).parent.get(5), 0);
});

test("the menu offers deleting several tags chosen together", () => {
  const many = tagMenu(MAP, [], [1, 9]).find((x) => x.id === "delete")!;
  assert.deepEqual([many.label, many.act, many.disabled], ["Remove or delete these 2 tags…", "delete", undefined]);
  assert.ok(/can't be edited/.test(tagMenu(MAP, [], [1, 9], null, false).find((x) => x.id === "delete")!.disabled!));
});
