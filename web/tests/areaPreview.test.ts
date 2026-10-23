// Pending edits that make or reshape tags, shown at once (#427): a new tag from an area of untagged content or a
// split, and content moved between tags. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap } from "../src/api";
import { contentMoves, newTags, splitKeeps, splitKeptText } from "../src/viewer/areaPreview";

type Spec = Partial<TagElement> & { i: number; role: string; parent: number | null };
function doc(specs: Spec[], more: Partial<TagMap> = {}): TagMap {
  const elements = specs.map((s): TagElement => ({
    kids: specs.filter((k) => k.parent === s.i).map((k) => k.i), boxes: [], own: false, order: null, ...s,
  } as TagElement));
  return { schema: 1, pages: [{ w: 600, h: 800 }, { w: 600, h: 800 }], elements, ...more };
}

// A heading, a paragraph of two pieces, a figure of one piece (and a drawing outside the page's stream), and two
// untagged sections at the foot of page 1
const MAP = doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "H1", parent: 0, own: true, text: "Results", boxes: [{ page: 1, bbox: [50, 40, 300, 70] }],
    parts: [{ k: 0, kind: "content", page: 1, bbox: [50, 40, 300, 70], text: "Results" }] },
  { i: 2, role: "P", parent: 0, own: true, text: "First line. Second line.", boxes: [{ page: 1, bbox: [50, 100, 500, 160] }],
    parts: [{ k: 0, kind: "content", page: 1, bbox: [50, 100, 500, 125], text: "First line." },
            { k: 1, kind: "content", page: 1, bbox: [50, 135, 480, 160], text: "Second line." }] },
  { i: 3, role: "Figure", parent: 0, own: true, boxes: [{ page: 1, bbox: [50, 200, 400, 400] }],
    parts: [{ k: 0, kind: "content", page: 1, bbox: [50, 200, 400, 300] },
            { k: 1, kind: "content", page: 1, bbox: [50, 300, 400, 400] }] },
], {
  pieces: [
    { page: 1, mcid: 0, el: 1, bbox: [50, 40, 300, 70], kind: "text", text: "Results" },
    { page: 1, mcid: 1, el: 2, bbox: [50, 100, 500, 125], kind: "text", text: "First line." },
    { page: 1, mcid: 2, el: 2, bbox: [50, 135, 480, 160], kind: "text", text: "Second line." },
    { page: 1, mcid: 3, el: 3, bbox: [50, 200, 400, 300], kind: "drawing" },
  ],
  artifacts: [
    { page: 1, seq: 4, bbox: [50, 700, 300, 720], kinds: ["text"], type: "", text: "Source: survey" },
    { page: 1, seq: 5, bbox: [310, 700, 550, 725], kinds: ["text"], type: "", text: "of 2026" },
  ],
});

test("an area of untagged content becomes a new tag, shown where it will be read", () => {
  const [t] = newTags(MAP, [{ op: "tag_artifacts", element: 3, expect_role: "Figure", position: "after", page: 1,
    sections: [4, 5], role: "P" }]);
  assert.deepEqual(t, { anchor: 3, position: "after", role: "P", alt: "", text: "Source: survey of 2026",
    boxes: [{ page: 1, bbox: [50, 700, 550, 725] }], op: 0 });
  const [fig] = newTags(MAP, [{ op: "tag_artifacts", element: 1, expect_role: "H1", position: "before", page: 1,
    sections: [4], role: "Figure", alt: "Logo" }]);
  assert.equal(fig.position, "before");
  assert.equal(fig.alt, "Logo");
});

test("a split makes a tag of the same type from the cut on; the tag keeps what's before it", () => {
  const ops = [{ op: "split" as const, element: 2, expect_role: "P", at: 1 }];
  const [t] = newTags(MAP, ops);
  assert.deepEqual(t, { anchor: 2, position: "after", role: "P", alt: "", text: "Second line.",
    boxes: [{ page: 1, bbox: [50, 135, 480, 160] }], op: 0 });
  // The new part's own type, chosen before saving (#427)
  assert.equal(newTags(MAP, [{ ...ops[0], role: "H2" }])[0].role, "H2");
  assert.deepEqual(splitKeeps(MAP, ops).get(2), [{ page: 1, bbox: [50, 100, 500, 125] }]);
  // Inside a piece's text: the new tag starts at that character, and the piece is in both
  const inside = [{ op: "split" as const, element: 2, expect_role: "P", at: 0, char: 6 }];
  assert.equal(newTags(MAP, inside)[0].text, "line. Second line.");
  assert.deepEqual(splitKeeps(MAP, inside).get(2), [{ page: 1, bbox: [50, 100, 500, 125] }]);
  // One piece whose text the tag map keeps on the tag: the tag's own text, cut at the same character
  const one = doc([{ i: 0, role: "P", parent: null, own: true, text: "Permits by lot. Each is valid.",
    parts: [{ k: 0, kind: "content", page: 1, bbox: [50, 100, 500, 125] }] }]);
  assert.equal(newTags(one, [{ op: "split", element: 0, expect_role: "P", at: 0, char: 16 }])[0].text, "Each is valid.");
  // No parts listed at all: the same, in the tag's box
  const bare = doc([{ i: 0, role: "P", parent: null, own: true, text: "Permits by lot. Each is valid.",
    boxes: [{ page: 1, bbox: [50, 100, 500, 125] }] }]);
  const [b] = newTags(bare, [{ op: "split", element: 0, expect_role: "P", at: 0, char: 16 }]);
  assert.deepEqual([b.text, b.boxes], ["Each is valid.", [{ page: 1, bbox: [50, 100, 500, 125] }]]);
  // ...and the tag reads only what it keeps
  assert.equal(splitKeptText(bare, [{ op: "split", element: 0, expect_role: "P", at: 0, char: 16 }]).get(0), "Permits by lot.");
  assert.equal(splitKeptText(MAP, ops).get(2), "First line.");
});

test("content moved into a tag grows it, the tag it came from shrinks, and one left empty goes", () => {
  const some = contentMoves(MAP, [{ op: "assign", element: 1, expect_role: "H1", pieces: [{ page: 1, mcid: 1 }] }]);
  assert.ok(!some.boxes.has(1));  // the heading keeps its own outline; what it gains is drawn beside it
  assert.deepEqual(some.boxes.get(2), [{ page: 1, bbox: [50, 135, 480, 160] }]);
  assert.deepEqual(some.added.get(1), [{ page: 1, bbox: [50, 100, 500, 125] }]);
  assert.equal(some.emptied.size, 0);

  const all = contentMoves(MAP, [{ op: "assign", element: 1, expect_role: "H1",
    pieces: [{ page: 1, mcid: 1 }, { page: 1, mcid: 2 }] }]);
  assert.deepEqual([...all.emptied], [2]);
  assert.ok(!all.boxes.has(2));
});

test("a tag with content the pieces don't cover keeps its boxes; pieces it already holds don't count", () => {
  // The figure's second part isn't a piece (drawn outside the page's own stream): it isn't redrawn from its pieces
  const moved = contentMoves(MAP, [{ op: "assign", element: 2, expect_role: "P", pieces: [{ page: 1, mcid: 3 }] }]);
  assert.ok(!moved.boxes.has(3) && !moved.emptied.has(3));
  assert.deepEqual(moved.added.get(2), [{ page: 1, bbox: [50, 200, 400, 300] }]);
  const none = contentMoves(MAP, [{ op: "assign", element: 2, expect_role: "P", pieces: [{ page: 1, mcid: 1 }] }]);
  assert.equal(none.boxes.size + none.added.size + none.emptied.size, 0);
});
