// Tags unsaved changes make, as tags (#692): numbered after the saved ones, named by their edit's id when the changes
// are kept, and taken back with the changes made to them. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagEdit, TagElement, TagMap } from "../src/api";
import { expand, isMade, mapRefs, prune, store, takeBack, withIds } from "../src/viewer/made";

type Spec = Partial<TagElement> & { i: number; role: string; parent: number | null };
function doc(specs: Spec[], more: Partial<TagMap> = {}): TagMap {
  const elements = specs.map((s): TagElement => ({
    kids: specs.filter((k) => k.parent === s.i).map((k) => k.i), boxes: [], own: false, order: null, ...s,
  } as TagElement));
  return { schema: 1, pages: [{ w: 600, h: 800 }], elements, ...more };
}

// A section with a heading and a paragraph, a second section, and two untagged lines
const MAP = doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "Sect", parent: 0 },
  { i: 2, role: "H1", parent: 1, own: true, text: "Results", boxes: [{ page: 1, bbox: [50, 40, 300, 70] }] },
  { i: 3, role: "P", parent: 1, own: true, text: "First line.", boxes: [{ page: 1, bbox: [50, 100, 500, 125] }] },
  { i: 4, role: "Sect", parent: 0 },
], {
  artifacts: [
    { page: 1, seq: 0, bbox: [50, 700, 300, 720], kinds: ["text"], type: "", text: "Source: survey" },
    { page: 1, seq: 1, bbox: [50, 730, 300, 750], kinds: ["text"], type: "", text: "Page 1" },
  ],
});

const AREA = { op: "tag_artifacts", id: "a1", element: 3, expect_role: "P", position: "after", page: 1, sections: [0],
  role: "P" } as TagEdit;
const made = (id: string, group = 0) => ({ made: id, group }) as unknown as number;

test("a new tag is a tag of the view, and changes name it by number there", () => {
  const stored = [AREA, { op: "set_role", element: made("a1"), expect_role: "P", role: "Caption" } as TagEdit];
  const v = expand(MAP, stored);
  assert.equal(v.base, 5);
  assert.deepEqual(v.made, [{ i: 5, op: 0, group: 0, id: "a1" }]);
  assert.deepEqual(v.map.elements[1].kids, [2, 3, 5]);
  assert.equal(v.map.elements[5].text, "Source: survey");
  assert.equal((v.ops[1] as { element: number }).element, 5);
  assert.equal(v.stored, stored);
  // Nothing made: the map and the edits as they are
  const plain = [{ op: "set_role", element: 3, expect_role: "P", role: "H2" } as TagEdit];
  assert.equal(expand(MAP, plain).map, MAP);
  assert.equal(expand(MAP, plain).ops, plain);
});

test("changes made on the view's numbers are kept with the new tags' names", () => {
  const v = expand(MAP, [AREA]);
  // Moved into the second section, then a tag placed after it: both name it by number in the view
  const next = [...v.ops,
    { op: "move", element: 5, expect_role: "P", target: 4, expect_target_role: "Sect", position: "in" } as TagEdit,
    { op: "move", element: 2, expect_role: "H1", target: 5, expect_target_role: "P", position: "after" } as TagEdit];
  const kept = store(v, next);
  assert.equal(kept.dropped, 0);
  assert.equal(kept.ops[0], AREA, "an edit left as it was is kept as it was");
  assert.deepEqual((kept.ops[1] as { element: unknown }).element, { made: "a1", group: 0 });
  assert.deepEqual((kept.ops[2] as { target: unknown }).target, { made: "a1", group: 0 });
  // ...and come back as numbers, with the tree as they leave it
  const again = expand(MAP, kept.ops);
  assert.deepEqual(again.ops.slice(1).map((o) => [(o as { element: number }).element, (o as { target: number }).target]), [[5, 4], [2, 5]]);
});

test("an edit that makes a tag is given an id; ids are kept", () => {
  const [a, b] = withIds([{ ...AREA, id: undefined } as TagEdit, AREA]);
  assert.ok(/^t[a-z0-9]+$/.test((a as { id: string }).id));
  assert.equal(b, AREA);
  const moves = [{ op: "move", element: 1, expect_role: "P", target: 2, expect_target_role: "P", position: "before" } as TagEdit];
  assert.equal(withIds(moves), moves);
});

test("taking back a new tag takes the changes made to it, in turn", () => {
  const stored = [AREA,
    { op: "set_alt", element: 2, expect_role: "H1", alt: "x" } as TagEdit,
    { op: "split", id: "s1", element: made("a1"), expect_role: "P", at: 1 } as TagEdit,
    { op: "set_lang", element: made("s1"), expect_role: "P", lang: "fr" } as TagEdit];
  const back = takeBack(stored, 0);
  assert.deepEqual(back.ops, [stored[1]]);
  assert.equal(back.dropped, 2); // the split of it, and the language of the split's new part
  // The same when its edit is taken out another way (from the list of changes)
  const v = expand(MAP, stored);
  assert.equal(store(v, v.ops.slice(1)).dropped, 2);
});

test("one of an area's new tags taken back: the names of the others follow", () => {
  const REGROUP = { op: "regroup", id: "g", element: 3, expect_role: "P", position: "after", page: 1,
    groups: [{ role: "P", sections: [0] }, { role: "P", sections: [1] }] } as TagEdit;
  const stored = [REGROUP,
    { op: "set_role", element: made("g", 0), expect_role: "P", role: "Caption" } as TagEdit,
    { op: "set_role", element: made("g", 1), expect_role: "P", role: "H2" } as TagEdit];
  const back = takeBack(stored, 0, 0);
  assert.equal(back.dropped, 1);
  assert.deepEqual((back.ops[0] as { groups: unknown[] }).groups, [{ role: "P", sections: [1] }]);
  assert.deepEqual((back.ops[1] as { element: unknown }).element, { made: "g", group: 0 });
  assert.equal(expand(MAP, back.ops).map.elements[5].text, "Page 1");
});

test("a name no earlier edit makes is taken out; tags are found wherever an edit names them", () => {
  assert.ok(isMade({ made: "a", group: 0 }) && !isMade(3) && !isMade({ made: 1 }));
  const early = [{ op: "set_role", element: made("a1"), expect_role: "P", role: "H2" } as TagEdit, AREA];
  assert.deepEqual(prune(early), { ops: [AREA], dropped: 1 });
  const names: unknown[] = [];
  mapRefs({ op: "rebuild_table", element: 1, expect_role: "Table", rows: [[{ from: [2, [3, 1]], role: "TD" }]],
    caption: 4, cuts: { 3: [{ at: 1 }] }, out: [{ role: "P", from: [5] }] } as TagEdit, (v) => { names.push(v); return v; });
  assert.deepEqual(names, [1, 4, 2, 3, 5, 3]);
});

// A paragraph of three lines, a figure of two pieces, and a table of one row
const BUILT = doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "P", parent: 0, own: true, text: "Steps One Two", order: 1, boxes: [{ page: 1, bbox: [50, 100, 300, 160] }],
    parts: [{ k: 0, kind: "content", page: 1, bbox: [50, 100, 300, 120], text: "Steps" },
            { k: 1, kind: "content", page: 1, bbox: [50, 120, 300, 140], text: "One" },
            { k: 2, kind: "content", page: 1, bbox: [50, 140, 300, 160], text: "Two" }] },
  { i: 2, role: "Figure", parent: 0, own: true, alt: "Two drawings", order: 2, boxes: [{ page: 1, bbox: [50, 200, 400, 300] }] },
  { i: 3, role: "Table", parent: 0, boxes: [{ page: 1, bbox: [50, 400, 400, 420] }] },
  { i: 4, role: "TR", parent: 3, boxes: [{ page: 1, bbox: [50, 400, 400, 420] }] },
  { i: 5, role: "TD", parent: 4, own: true, text: "a", boxes: [{ page: 1, bbox: [50, 400, 200, 420] }] },
  { i: 6, role: "TD", parent: 4, own: true, text: "b", boxes: [{ page: 1, bbox: [200, 400, 400, 420] }] },
], { pieces: [
  { page: 1, mcid: 7, el: 2, bbox: [50, 200, 200, 300], kind: "drawing" },
  { page: 1, mcid: 8, el: 2, bbox: [250, 200, 400, 300], kind: "drawing" },
] });

test("a list built from a paragraph's lines: its heading, list and items are tags, in the paragraph's place", () => {
  const v = expand(BUILT, [{ op: "build_list", id: "b", element: 1, expect_role: "P",
    rows: [{ at: 0, as: "H3" }, { at: 1, level: 1 }, { at: 2, level: 1 }] } as TagEdit]);
  assert.deepEqual(v.made.map((m) => [m.group, v.map.elements[m.i].role, v.map.elements[m.i].text ?? ""]),
    [[0, "H3", "Steps"], [1, "L", ""], [2, "LI", "One"], [3, "LI", "Two"]]);
  assert.deepEqual(v.map.elements[0].kids, [7, 8, 2, 3]);       // the heading and the list where the paragraph was
  assert.deepEqual(v.map.elements[8].kids, [9, 10]);            // the items in the list
  assert.deepEqual(v.map.elements[1].boxes, []);                // the paragraph isn't drawn
});

test("a table made or rebuilt: the table, its rows and cells are tags; a rebuilt one says which table it is", () => {
  const rebuilt = expand(BUILT, [{ op: "rebuild_table", id: "r", element: 3, expect_role: "Table",
    rows: [[{ from: [5], role: "TH", scope: "Column" }, { from: [6], role: "TD" }]] } as TagEdit]);
  assert.deepEqual(rebuilt.made.map((m) => rebuilt.map.elements[m.i].role), ["Table", "TR", "TH", "TD"]);
  const table = rebuilt.map.elements[rebuilt.made[0].i];
  assert.equal(table.of, 3);
  assert.deepEqual(rebuilt.map.elements[0].kids, [1, 2, 7]);
  assert.deepEqual(rebuilt.map.elements[8].kids, [9, 10]);
  const made = expand(BUILT, [{ op: "build_table", id: "t", element: 1, expect_role: "P",
    rows: [[{ parts: [{ at: 0 }], role: "TH" }, { parts: [{ at: 1 }, { at: 2 }], role: "TD" }]] } as TagEdit]);
  assert.deepEqual(made.made.map((m) => [made.map.elements[m.i].role, made.map.elements[m.i].text ?? ""]),
    [["Table", ""], ["TR", ""], ["TH", "Steps"], ["TD", "One Two"]]);
  assert.equal(made.map.elements[made.made[0].i].of, undefined);
});

test("a figure's part is a tag right after the figure", () => {
  const v = expand(BUILT, [{ op: "take_pieces", id: "f", element: 2, expect_role: "Figure", pieces: [{ page: 1, mcid: 8 }],
    role: "Figure", alt: "The right drawing" } as TagEdit]);
  const part = v.map.elements[v.made[0].i];
  assert.deepEqual([part.role, part.alt, part.boxes], ["Figure", "The right drawing", [{ page: 1, bbox: [250, 200, 400, 300] }]]);
  assert.deepEqual(v.map.elements[0].kids, [1, 2, 7, 3]);
});

test("an area's new tag from a deleted tag's content goes by the anchor, as the engine places it", () => {
  const ops = [{ op: "delete", element: 3, expect_role: "P", children: "delete" } as TagEdit,
    { op: "regroup", id: "g", element: 2, expect_role: "H1", position: "before", page: 1,
      groups: [{ role: "P", pieces: [9] }] } as TagEdit];
  const map = { ...MAP, pieces: [{ page: 1, mcid: 9, el: 3, bbox: [50, 100, 500, 125], kind: "text", text: "First line." }] } as TagMap;
  const v = expand(map, ops);
  assert.deepEqual(v.map.elements[1].kids, [5, 2, 3]);  // before the heading (the anchor), not where the paragraph was
});
