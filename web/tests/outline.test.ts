// Building a list from a paragraph's lines. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagPart } from "../src/api";
import { builtPreview, builtSummary, canIndent, canOutdent, changeLine, outlineEdit, outlineLines, outlineRows, outlineSummary } from "../src/viewer/outline";
import { tagMenu } from "../src/viewer/menu";
import { absorbedBy, describe, editMarks, withoutEdits } from "../src/viewer/draft";

const part = (k: number, x: number, y: number, text: string, w = 120): TagPart => ({ k, kind: "content", page: 1, bbox: [x, y, x + w, y + 12], text });
const para = (i: number, parts: TagPart[], more: Partial<TagElement> = {}): TagElement => ({
  i, parent: 0, kids: [], role: "P", page: 1, own: true, order: i, text: parts.map((p) => p.text).join(" "),
  boxes: [{ page: 1, bbox: [100, parts[0].bbox![1], 500, parts[parts.length - 1].bbox![3]] }], parts, ...more,
} as TagElement);

// An outline read as one paragraph: a numbered top line (its number a piece of its own), three points under it,
// the third with two sub-points, one of which wraps, and a last point that wraps too
const outline = () => para(1, [
  part(0, 107, 100, "4.", 20), part(1, 143, 100, "Looking after the garden"),
  part(2, 143, 114, "4.1. Water in the morning"),
  part(3, 143, 128, "4.2. Feed once a month"),
  part(4, 143, 142, "4.3. Things to watch for:"),
  part(5, 161, 156, "4.3.1. Leaves that curl at the edges or"),
  part(6, 203, 170, "turn yellow"),
  part(7, 161, 184, "4.3.2. Soil that stays wet"),
  part(8, 143, 198, "4.4. Cut back in autumn, once the first"),
  part(9, 179, 212, "frost has passed"),
]);

test("the first guess: numbered lines start rows, wrapped lines carry on, depth follows the left edge", () => {
  const lines = outlineLines([outline()]);
  assert.deepEqual(lines.map((l) => l.k), [0, 2, 3, 4, 5, 6, 7, 8, 9]);           // the number and its text are one line
  assert.equal(lines[0].text, "4. Looking after the garden");
  assert.deepEqual(lines.map((l) => l.start), [true, true, true, true, true, false, true, true, false]);
  assert.deepEqual(lines.filter((l) => l.start).map((l) => l.indent), [0, 1, 1, 1, 2, 2, 1]);
  assert.deepEqual(outlineRows(lines).map((r) => r.more.length), [0, 0, 0, 0, 1, 0, 1]);
  assert.equal(outlineSummary(lines), "7 list items (6 nested)");
});

test("the top line as a heading: the list under it starts at the top level", () => {
  const lines = changeLine(outlineLines([outline()]), 0, { kind: "H3" });
  assert.deepEqual(lines.filter((l) => l.start).map((l) => l.indent), [0, 0, 0, 0, 1, 1, 0]);
  assert.equal(outlineSummary(lines), "1 heading, 6 list items (2 nested)");
  assert.deepEqual(outlineEdit([outline()], lines), { op: "build_list", element: 1, expect_role: "P", rows: [
    { at: 0, as: "H3" }, { at: 2, level: 1 }, { at: 3, level: 1 }, { at: 4, level: 1 }, { at: 5, level: 2 }, { at: 7, level: 2 }, { at: 8, level: 1 },
  ] });
});

test("rows move a level at a time and never deeper than one under the row before", () => {
  let lines = changeLine(outlineLines([outline()]), 0, { kind: "H3" });
  assert.equal(canIndent(lines, 1), false);                                        // the first item has nothing to go under
  assert.equal(canIndent(lines, 2), true);
  assert.equal(canOutdent(lines, 2), false);
  lines = changeLine(lines, 2, { indent: 1 });
  assert.deepEqual(lines.filter((l) => l.start).map((l) => l.indent), [0, 0, 1, 0, 1, 1, 0]);
  assert.equal(canIndent(lines, 2), false);                                        // already one under the row before
  lines = changeLine(lines, 4, { indent: -1 });                                    // the next sub-point keeps to the rule
  assert.deepEqual(lines.filter((l) => l.start).map((l) => l.indent), [0, 0, 1, 0, 0, 1, 0]);
  // a paragraph in the middle ends the list: what follows starts again at the top
  lines = changeLine(lines, 4, { kind: "P" });
  assert.deepEqual(outlineEdit([outline()], lines).op === "build_list" && (outlineEdit([outline()], lines) as { rows: unknown[] }).rows.slice(4),
    [{ at: 5, as: "P" }, { at: 7, level: 1 }, { at: 8, level: 1 }]);
});

test("a wrongly guessed line can be joined to the row above, or made its own row", () => {
  let lines = outlineLines([outline()]);
  lines = changeLine(lines, 5, { start: true });                                   // the wrapped line as its own row
  assert.equal(lines[5].start, true);
  assert.equal(lines[5].indent, lines[4].indent);
  lines = changeLine(lines, 6, { start: false });
  assert.deepEqual(outlineRows(lines).map((r) => r.more.length), [0, 0, 0, 0, 0, 1, 1]);
  assert.equal(changeLine(lines, 0, { start: false })[0].start, true);             // the first line always starts a row
});

test("lines with no numbers or bullets are each a row; several tags follow each other", () => {
  const a = para(1, [part(0, 100, 100, "Bread and butter"), part(1, 100, 114, "Tea with milk")]);
  const b = para(2, [part(0, 100, 140, "Two apples")]);
  const lines = outlineLines([a, b]);
  assert.deepEqual(lines.map((l) => [l.el, l.k, l.start, l.indent]), [[1, 0, true, 0], [1, 1, true, 0], [2, 0, true, 0]]);
  assert.deepEqual(outlineEdit([a, b], lines), { op: "build_list", element: 1, expect_role: "P", absorb: [2],
    rows: [{ at: 0, level: 1 }, { at: 1, level: 1 }, { el: 2, at: 0, level: 1 }] });
});

test("the menu offers it for text with lines, and the unsaved list is shown and can be undone", () => {
  const p = outline();
  const map = { schema: 1, pages: [{ w: 600, h: 800 }], elements: [{ i: 0, parent: null, kids: [1, 2], role: "Document", boxes: [], own: false, order: null } as TagElement,
    p, para(2, [part(0, 100, 300, "One line only")])] };
  const offer = (ids: number[], ops = [] as Parameters<typeof tagMenu>[1]) => tagMenu(map, ops, ids).find((x) => x.id === "build");
  assert.equal(offer([1])!.act, "build");
  assert.equal(offer([2]), undefined);                                             // one line: nothing to build from
  assert.equal(offer([1, 2])!.disabled, undefined);
  const spoken = { ...map, elements: [map.elements[0], { ...p, actual_text: "The garden outline" }, map.elements[2]] };
  assert.ok(/stands for all of it/.test(tagMenu(spoken, [], [1]).find((x) => x.id === "build")!.disabled!));
  const op = outlineEdit([p, map.elements[2]], outlineLines([p, map.elements[2]]));
  assert.ok(/unsaved change/.test(offer([1], [op])!.disabled!));                   // not twice
  assert.equal(absorbedBy([op]).get(2), 1);
  assert.deepEqual(editMarks(map, [op]).get(1), ["list built"]);
  assert.ok(/built into 8 list items/.test(describe(op, map)));
  assert.deepEqual(withoutEdits(map, [op], [2]), []);                              // undone from a tag it took
});

test("an applied list is drawn as it will be saved: heading, list, items, and the nested list under its item", () => {
  const p = outline();
  const map = { schema: 1, pages: [{ w: 600, h: 800 }], elements: [{ i: 0, parent: null, kids: [1], role: "Document", boxes: [], own: false, order: null } as TagElement, p] };
  const op = outlineEdit([p], changeLine(outlineLines([p]), 0, { kind: "H3" })) as Extract<ReturnType<typeof outlineEdit>, { op: "build_list" }>;
  const rows = builtPreview(map, op);
  assert.deepEqual(rows.map((r) => [r.role, r.depth]), [["H3", 0], ["L", 0], ["LI", 1], ["LI", 1], ["LI", 1], ["L", 2], ["LI", 3], ["LI", 3], ["LI", 1]]);
  assert.equal(rows[0].text, "4. Looking after the garden");
  assert.equal(rows[6].text, "4.3.1. Leaves that curl at the edges or turn yellow");   // a wrapped line, whole
  assert.equal(builtSummary(rows), "builds 1 heading or paragraph and a list of 6 items, 2 of them nested");
  // a paragraph between two runs of items: a new list starts after it
  const split = builtPreview(map, { ...op, rows: [{ at: 0, level: 1 }, { at: 2, as: "P" }, { at: 3, level: 1 }] });
  assert.deepEqual(split.map((r) => r.role), ["L", "LI", "P", "L", "LI"]);
});

test("an applied list has a box for each row on the page, and each list goes round its items", () => {
  const p = outline();
  const map = { schema: 1, pages: [{ w: 600, h: 800 }], elements: [{ i: 0, parent: null, kids: [1], role: "Document", boxes: [], own: false, order: null } as TagElement, p] };
  const op = outlineEdit([p], changeLine(outlineLines([p]), 0, { kind: "H3" })) as Extract<ReturnType<typeof outlineEdit>, { op: "build_list" }>;
  const rows = builtPreview(map, op);
  const box = (n: number) => rows[n].boxes.map((b) => [b.page, ...b.bbox]);
  assert.deepEqual(box(0), [[1, 107, 100, 263, 112]]);              // the heading: its number and its words
  assert.deepEqual(box(6), [[1, 161, 156, 323, 182]]);              // a wrapped item: both its lines
  assert.deepEqual(box(5), [[1, 161, 156, 323, 196]]);              // the nested list: round its two items
  assert.deepEqual(box(1), [[1, 143, 114, 323, 224]]);              // the list: everything under the heading
});
