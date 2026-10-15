// The viewer's pending-edit logic. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagEdit, TagElement, TagMap } from "../src/api";
import { draftTree, editMarks, jumpQuestion, pageOrder, tagPages, tagsOnPage, undoMoves } from "../src/viewer/draft";

type Spec = Partial<TagElement> & { i: number; role: string; parent: number | null };

function doc(specs: Spec[]): TagMap {
  let order = 0;
  const elements = specs.map((s): TagElement => ({
    kids: specs.filter((k) => k.parent === s.i).map((k) => k.i),
    boxes: s.own ? [{ page: s.page ?? 1, bbox: [0, 0, 10, 10] }] : [],
    own: false,
    ...s,
    order: s.own ? order++ : null,
  } as TagElement));
  for (const e of [...elements].reverse()) {
    if (e.parent !== null) elements[e.parent].boxes.push(...e.boxes);
  }
  return { schema: 1, pages: [{ w: 600, h: 800 }, { w: 600, h: 800 }], elements };
}

const sample = () => doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "H1", parent: 0, own: true, text: "Title" },
  { i: 2, role: "Sect", parent: 0 },
  { i: 3, role: "P", parent: 2, own: true, text: "One" },
  { i: 4, role: "Link", parent: 3 },                       // only an annotation: no place on a page
  { i: 5, role: "P", parent: 2, own: true, text: "Two", page: 2 },
  { i: 6, role: "Table", parent: 0 },
  { i: 7, role: "TR", parent: 6 },
  { i: 8, role: "TD", parent: 7, own: true, text: "Cell", page: 2 },
]);

const move = (element: number, target: number, position: "before" | "after"): TagEdit =>
  ({ op: "move", element, expect_role: "P", target, expect_target_role: "P", position });

test("one page's tags: its content, the groups around it, and tags with no place of their own", () => {
  const map = sample();
  assert.deepEqual([...tagsOnPage(map, 1)].sort((a, b) => a - b), [0, 1, 2, 3, 4]);
  assert.deepEqual([...tagsOnPage(map, 2)].sort((a, b) => a - b), [0, 2, 5, 6, 7, 8]);
});

test("undoing a tag's move leaves every other pending change", () => {
  const map = sample();
  const ops: TagEdit[] = [
    move(3, 1, "before"),
    { op: "set_role", element: 3, expect_role: "P", role: "H2" },
    move(5, 3, "before"),
    move(3, 5, "after"),
  ];
  const left = undoMoves(ops, 3);
  assert.deepEqual(left, [ops[1], ops[2]]);
  assert.deepEqual(editMarks(map, left).get(3), ["type changed"]);
  assert.deepEqual(editMarks(map, left).get(5), ["moved"]);
  // ...and the tree and the reading order are back as they were for that tag
  assert.equal(draftTree(map, left).parent.get(3), 2);
  assert.deepEqual(pageOrder(map, 1, left).map((e) => e.i), [1, 3]);
  assert.deepEqual(pageOrder(map, 1, ops).map((e) => e.i), [3, 1]);
});

test("a move shows the same in the tree and in the page's reading order", () => {
  const map = sample();
  const ops = [move(1, 3, "after")];
  assert.deepEqual(pageOrder(map, 1, ops).map((e) => e.i), [3, 1]);
  assert.deepEqual(draftTree(map, ops).kids.get(2), [3, 1, 5]);
});

test("deleting a tag: with what's inside, or lifting its children into its place", () => {
  const map = sample();
  const lift: TagEdit[] = [{ op: "delete", element: 2, expect_role: "Sect", children: "lift" }];
  const t1 = draftTree(map, lift);
  assert.deepEqual(t1.kids.get(0), [1, 3, 5, 6]);            // the Sect's paragraphs take its place
  assert.equal(t1.parent.get(3), 0);
  assert.deepEqual([...t1.deleted], [2]);
  assert.deepEqual(pageOrder(map, 1, lift).map((e) => e.i), [1, 3]);  // still read
  assert.deepEqual(editMarks(map, lift).get(0), ["tag removed"]);

  const all: TagEdit[] = [{ op: "delete", element: 6, expect_role: "Table", children: "delete" }];
  const t2 = draftTree(map, all);
  assert.deepEqual(t2.kids.get(0), [1, 2]);
  assert.deepEqual([...t2.deleted].sort(), [6, 7, 8]);
  assert.deepEqual(pageOrder(map, 2, all).map((e) => e.i), [5]);      // the cell is no longer read
  assert.deepEqual(editMarks(map, all).get(0), ["tag deleted"]);
});

test("deleting a tag with everything in it keeps the links and fields inside, moved up into its place", () => {
  const map = sample();
  map.elements[4].annot = true;                               // the Link under the first paragraph of the Sect
  const all: TagEdit[] = [{ op: "delete", element: 2, expect_role: "Sect", children: "delete" }];
  const t = draftTree(map, all);
  assert.deepEqual(t.kids.get(0), [1, 4, 6]);                 // the link takes the section's place
  assert.equal(t.parent.get(4), 0);
  assert.deepEqual([...t.deleted].sort(), [2, 3, 5]);         // the section and its paragraphs go; the link doesn't
  assert.deepEqual(t.kids.get(3), []);
});


test("one-page view asks before turning the page for a tag that spans pages", () => {
  const map = sample();
  const sect = map.elements[2];                       // paragraphs on pages 1 and 2
  assert.deepEqual(tagPages(sect), [1, 2]);
  assert.equal(jumpQuestion(sect, 1), null);          // it starts on the page shown: nothing to ask
  assert.deepEqual(jumpQuestion(sect, 2), { first: 1, last: 2, pages: 2, here: true });
  assert.equal(jumpQuestion(map.elements[5], 1), null);  // a tag on one page just goes there, as before
  const top = map.elements[0];
  assert.deepEqual(jumpQuestion({ ...top, boxes: [{ page: 3, bbox: [0, 0, 1, 1] }, { page: 5, bbox: [0, 0, 1, 1] }] }, 4),
    { first: 3, last: 5, pages: 2, here: false });
});
