// The table editor's grid (#370): read from the tag map, edited (merge, split, rows and columns, headers), and
// turned into the one edit the engine applies. A removed row's or column's content joins its neighbour.
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap } from "../src/api";
import {
  addColumn, addRow, buildEdit, canMerge, cellWords, cutCell, fromLayout, fromOp, fromTable, headerCols, headerRows,
  merge, partTexts, problems, rebuildEdit, removeColumn, removeRow, setHeaders, setSay, split, tablePreview,
  tableReplaced, takeOutColumn, toggleHeader, type Grid,
} from "../src/viewer/tablegrid";

type Spec = Partial<TagElement> & { i: number; role: string; parent: number | null };
function doc(specs: Spec[]): TagMap {
  const elements = specs.map((s): TagElement => ({
    kids: specs.filter((k) => k.parent === s.i).map((k) => k.i), boxes: [], own: false, order: null, ...s,
  } as TagElement));
  return { schema: 1, pages: [{ w: 600, h: 800 }], elements };
}

// Quarter | Documents | Pass rate / Q1 | 120 | 64% / Q2 | 180 | 71%, with a caption before it
const TABLE = doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "P", parent: 0, text: "Results by quarter" },
  { i: 2, role: "Table", parent: 0 },
  { i: 3, role: "TR", parent: 2 },
  { i: 4, role: "TH", parent: 3, text: "Quarter", scope: "Column" },
  { i: 5, role: "TH", parent: 3, text: "Documents", scope: "Column" },
  { i: 6, role: "TH", parent: 3, text: "Pass rate", scope: "Column" },
  { i: 7, role: "TR", parent: 2 },
  { i: 8, role: "TD", parent: 7, text: "Q1" },
  { i: 9, role: "TD", parent: 7, text: "120" },
  { i: 10, role: "TD", parent: 7, text: "64%" },
  { i: 11, role: "TR", parent: 2 },
  { i: 12, role: "TD", parent: 11, text: "Q2" },
  { i: 13, role: "TD", parent: 11, text: "180" },
  { i: 14, role: "TD", parent: 11, text: "71%" },
]);

const texts = (g: Grid) => {
  const out: string[][] = Array.from({ length: g.height }, () => []);
  for (const x of [...g.cells].sort((a, b) => a.r - b.r || a.c - b.c)) out[x.r].push(x.text || "·");
  return out;
};
const at = (g: Grid, r: number, c: number) => g.cells.find((x) => x.r === r && x.c === c)!;

test("a table is read from the tag map, and as it is it rebuilds to itself", () => {
  const g = fromTable(TABLE, 2);
  assert.deepEqual([g.height, g.width, headerRows(g), headerCols(g)], [3, 3, 1, 0]);
  assert.deepEqual(texts(g), [["Quarter", "Documents", "Pass rate"], ["Q1", "120", "64%"], ["Q2", "180", "71%"]]);
  const op = rebuildEdit(TABLE, 2, g);
  assert.deepEqual(op.rows[0][0], { from: [4], role: "TH", scope: "Column" });
  assert.deepEqual(op.rows[1].map((c) => c.from), [[8], [9], [10]]);
  assert.equal("caption" in op, false);  // unchanged
  assert.deepEqual(texts(fromOp(TABLE, op)), texts(g));
});

test("cells merge and split, keeping what they hold", () => {
  let g = fromTable(TABLE, 2);
  assert.ok(canMerge(g, at(g, 1, 0).key, "right"));
  g = merge(g, at(g, 1, 0).key, "right");
  assert.deepEqual([at(g, 1, 0).cols, at(g, 1, 0).from, at(g, 1, 0).text], [2, [8, 9], "Q1 120"]);
  assert.equal(canMerge(g, at(g, 0, 0).key, "down"), false);  // the cell below is wider now
  assert.equal(problems(g), null);
  g = split(g, at(g, 1, 0).key);
  assert.deepEqual(texts(g)[1], ["Q1 120", "·", "64%"]);
  assert.equal(problems(g), null);
});

test("a removed row joins the row above (the first, the row below); a removed column the one to its left", () => {
  let g = removeRow(fromTable(TABLE, 2), 2);
  assert.deepEqual(texts(g), [["Quarter", "Documents", "Pass rate"], ["Q1 Q2", "120 180", "64% 71%"]]);
  assert.deepEqual(at(g, 1, 0).from, [8, 12]);
  g = removeRow(fromTable(TABLE, 2), 0);
  assert.deepEqual(texts(g)[0], ["Quarter Q1", "Documents 120", "Pass rate 64%"]);  // the removed row read first
  assert.deepEqual(at(g, 0, 0).from, [4, 8]);
  g = removeColumn(fromTable(TABLE, 2), 2);
  assert.deepEqual(texts(g)[1], ["Q1", "120 64%"]);
  // Every old cell is still somewhere: nothing on the page goes untagged
  const all = rebuildEdit(TABLE, 2, g).rows.flat().flatMap((c) => c.from as number[]).sort((a, b) => a - b);
  assert.deepEqual(all, [4, 5, 6, 8, 9, 10, 12, 13, 14]);
});

test("rows and columns are added empty, and a spanning cell grows across them", () => {
  let g = merge(fromTable(TABLE, 2), at(fromTable(TABLE, 2), 1, 0).key, "down");  // Q1 over Q2's row
  g = addRow(g, 1);
  assert.equal(g.height, 4);
  assert.equal(at(g, 1, 0).rows, 3);                 // the merged cell spans the new row too
  assert.deepEqual(texts(g)[2], ["·", "·"]);         // the new row's other places are empty cells
  g = addColumn(g, -1);
  assert.equal(g.width, 4);
  assert.equal(at(g, 0, 0).role, "TH");              // a new column's place in a header row is a header
  assert.equal(problems(g), null);
});

test("header rows and columns, a cell at a time or all at once", () => {
  let g = setHeaders(fromTable(TABLE, 2), 1, 1);
  assert.deepEqual([at(g, 0, 0).scope, at(g, 0, 1).scope, at(g, 1, 0).scope, at(g, 1, 1).role], ["Both", "Column", "Row", "TD"]);
  assert.deepEqual([headerRows(g), headerCols(g)], [1, 1]);
  g = toggleHeader(g, at(g, 1, 1).key);
  assert.equal(at(g, 1, 1).role, "TH");
  g = setHeaders(g, 0, 0);
  assert.ok(g.cells.every((x) => x.role === "TD"));
});

test("the caption: a tag beside the table, or none", () => {
  const g = fromTable(TABLE, 2);
  assert.equal(rebuildEdit(TABLE, 2, { ...g, caption: 1 }).caption, 1);
  const captioned = doc([...TABLE.elements.map((e) => ({ ...e })), { i: 15, role: "Caption", parent: 2, text: "Results" }]);
  captioned.elements[2].kids = [15, 3, 7, 11];
  assert.equal(fromTable(captioned, 2).caption, 15);
  assert.equal(rebuildEdit(captioned, 2, { ...fromTable(captioned, 2), caption: null }).caption, null);
});

test("text tags are laid out as a table from where their pieces sit", () => {
  const box = (x: number, y: number): [number, number, number, number] => [x, y, x + 60, y + 12];
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "P", parent: 0, own: true, text: "Lot Price", boxes: [{ page: 1, bbox: box(72, 100) }],
      parts: [{ k: 0, kind: "content", bbox: box(72, 100), text: "Lot" }, { k: 1, kind: "content", bbox: box(300, 100), text: "Price" }] },
    { i: 2, role: "P", parent: 0, own: true, text: "North $145", boxes: [{ page: 1, bbox: box(72, 130) }],
      parts: [{ k: 0, kind: "content", bbox: box(72, 130), text: "North" }, { k: 1, kind: "content", bbox: box(302, 130), text: "$145" }] },
    { i: 3, role: "P", parent: 0, own: true, text: "Library", boxes: [{ page: 1, bbox: box(72, 160) }] },
  ]);
  const g = fromLayout(map, [1, 2, 3]);
  assert.deepEqual(texts(g), [["Lot", "Price"], ["North", "$145"], ["Library", "·"]]);
  assert.deepEqual([headerRows(g), problems(g)], [1, null]);
  const op = buildEdit(map, [1, 2, 3], g);
  assert.deepEqual([op.element, op.absorb], [1, [2, 3]]);
  assert.deepEqual(op.rows[0][1], { parts: [{ at: 1 }], role: "TH", scope: "Column" });
  assert.deepEqual(op.rows[2].map((c) => c.parts), [[{ el: 3, at: 0 }], []]);
});

test("a figure that is really a drawn table: its words make the cells, its ruled lines don't shape the grid", () => {
  const box = (x: number, y: number): [number, number, number, number] => [x, y, x + 50, y + 10];
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "Figure", parent: 0, own: true, alt: "A table", text: "Date Hours Signed",
      boxes: [{ page: 1, bbox: [30, 290, 460, 400] }],
      parts: [
        { k: 0, kind: "content" },                                         // ruled lines: no text, no place of their own
        { k: 1, kind: "content", bbox: box(40, 300), text: "Date" },
        { k: 2, kind: "content" },
        { k: 3, kind: "content", bbox: box(180, 300), text: "Hours" },
        { k: 4, kind: "content", bbox: box(320, 300), text: "Signed" },
        { k: 5, kind: "content" },
      ] },
  ]);
  const g = fromLayout(map, [1]);
  assert.deepEqual(texts(g), [["Date", "Hours", "Signed"]]);       // one header row, not everything in one cell
  assert.deepEqual(at(g, 0, 0).pieces, [{ el: 1, at: 1 }, { el: 1, at: 0 }, { el: 1, at: 2 }, { el: 1, at: 5 }]);
  // The drawn grid's empty rows are added in the editor
  const more = addRow(addRow(g, 0), 1);
  assert.deepEqual([more.height, problems(more)], [3, null]);
  const op = buildEdit(map, [1], more);
  assert.equal(op.rows.flat().flatMap((c) => c.parts).length, 6);   // every piece is somewhere
});

test("a pending table edit is previewed as it will be saved: rows, cells, their text and boxes", () => {
  const box = (x: number, y: number): [number, number, number, number] => [x, y, x + 50, y + 10];
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "P", parent: 0, own: true, text: "Lot Price", boxes: [{ page: 1, bbox: [72, 100, 352, 110] }],
      parts: [{ k: 0, kind: "content", page: 1, bbox: box(72, 100), text: "Lot" }, { k: 1, kind: "content", page: 1, bbox: box(300, 100), text: "Price" }] },
    { i: 2, role: "P", parent: 0, own: true, text: "North", boxes: [{ page: 1, bbox: box(72, 130) }] },
  ]);
  const op = buildEdit(map, [1, 2], fromLayout(map, [1, 2]));
  const rows = tablePreview(map, op);
  assert.deepEqual(rows.map((r) => r.role), ["Table", "TR", "TH", "TH", "TR", "TD", "TD"]);
  assert.deepEqual(rows.filter((r) => r.depth === 2).map((r) => r.text), ["Lot", "Price", "North", ""]);
  assert.deepEqual(rows[0].boxes, [{ page: 1, bbox: [72, 100, 350, 140] }]);  // round everything it holds
  assert.deepEqual(tableReplaced(map, op), [2]);                              // the other tag is hidden until saved
  // An edited table: its own rows and cells are replaced by the preview, and a caption moved in shows in it
  const rebuilt = rebuildEdit(TABLE, 2, { ...fromTable(TABLE, 2), caption: 1 });
  const preview = tablePreview(TABLE, rebuilt);
  assert.deepEqual(preview.slice(0, 2).map((r) => [r.role, r.text]), [["Table", ""], ["Caption", "Results by quarter"]]);
  assert.deepEqual(new Set(tableReplaced(TABLE, rebuilt)), new Set([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 1]));
});

// A switch table read off a picture (production: the Assembly Poster): a row's words all in its first cell, the cells
// beside it empty, and a paragraph set beside the table read as its last column
const SWITCHES = doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "Table", parent: 0 },
  { i: 2, role: "TR", parent: 1 },
  { i: 3, role: "TH", parent: 2, own: true, text: "MS1 MS2 Resolution", scope: "Column" },
  { i: 4, role: "TD", parent: 2 },
  { i: 5, role: "TD", parent: 2 },
  { i: 6, role: "TH", parent: 2, own: true, text: "These switches", scope: "Column" },
  { i: 7, role: "TR", parent: 1 },
  { i: 8, role: "TD", parent: 7, own: true, text: "OFF ON 1/2 Step" },
  { i: 9, role: "TD", parent: 7 },
  { i: 10, role: "TD", parent: 7 },
  { i: 11, role: "TD", parent: 7, own: true, text: "are optional." },
]);

test("a cell's words are cut into the cells beside it, and a column comes out as a paragraph", () => {
  let g = fromTable(SWITCHES, 1);
  const head = at(g, 0, 0);
  assert.deepEqual(cellWords(SWITCHES, head).map((w) => [w.text, w.cut]),
    [["MS1", null], ["MS2", { at: 0, char: 4 }], ["Resolution", { at: 0, char: 8 }]]);
  g = cutCell(SWITCHES, g, head.key, [1, 2]);
  g = cutCell(SWITCHES, g, at(g, 1, 0).key, [1, 2]);
  assert.deepEqual(texts(g), [["MS1", "MS2", "Resolution", "These switches"], ["OFF", "ON", "1/2 Step", "are optional."]]);
  assert.deepEqual(partTexts(SWITCHES, 8, [{ at: 0, char: 4 }, { at: 0, char: 7 }]), ["OFF", "ON", "1/2 Step"]);
  g = takeOutColumn(g, 3);
  assert.deepEqual(texts(g), [["MS1", "MS2", "Resolution"], ["OFF", "ON", "1/2 Step"]]);
  assert.equal(problems(g), null);
  const op = rebuildEdit(SWITCHES, 1, g);
  assert.deepEqual(op.rows[0].map((c) => c.from), [[[3, 0]], [[3, 1], 4], [[3, 2], 5]]);
  assert.deepEqual(op.cuts, { 3: [{ at: 0, char: 4 }, { at: 0, char: 8 }], 8: [{ at: 0, char: 4 }, { at: 0, char: 7 }] });
  assert.deepEqual(op.out, [{ role: "P", from: [6, 11] }]);
  // Reopened from the pending edit, and previewed with the paragraph after the table
  assert.deepEqual(texts(fromOp(SWITCHES, op)), texts(g));
  const preview = tablePreview(SWITCHES, op);
  assert.deepEqual(preview.at(-1), { role: "P", depth: 0, text: "These switches are optional.", boxes: [] });
  // A row running out of cells gets a column; a cut cell can't be cut again
  const narrow = takeOutColumn(fromTable(SWITCHES, 1), 3);
  const wider = cutCell(SWITCHES, narrow, at(narrow, 1, 0).key, [1, 2, 3]);
  assert.equal(wider.width, 4);
  assert.deepEqual(texts(wider)[1], ["OFF", "ON", "1/2", "Step"]);
  assert.deepEqual(cellWords(SWITCHES, at(g, 0, 0)), []);
});

test("a cell is set to be read as other words, in the table editor, and back to its drawn text", () => {
  let g = fromTable(SWITCHES, 1);
  g = cutCell(SWITCHES, g, at(g, 1, 0).key, [1, 2]);
  g = setSay(g, at(g, 1, 2).key, "1/2 step");  // a part of a cut cell too: it's read as this once saved
  const op = rebuildEdit(SWITCHES, 1, g);
  assert.equal(op.rows[1][2].text, "1/2 step");
  assert.equal("text" in op.rows[1][0], false);  // cells not set here are left as they are
  assert.equal(tablePreview(SWITCHES, op).find((r) => r.text === "1/2 step")?.role, "TD");
  assert.equal(fromOp(SWITCHES, op).cells.find((x) => x.r === 1 && x.c === 2)?.say, "1/2 step");
  // A cell already read as other words shows them; emptied, it goes back to its drawn text; merged, it's unset
  const said = doc(SWITCHES.elements.map((e) => (e.i === 11 ? { ...e, parent: e.parent, actual_text: "are optional" } : { ...e, parent: e.parent })));
  const h = fromTable(said, 1);
  assert.equal(at(h, 1, 3).say, "are optional");
  assert.equal(rebuildEdit(said, 1, setSay(h, at(h, 1, 3).key, "")).rows[1][3].text, "");
  assert.equal(merge(h, at(h, 1, 2).key, "right").cells.find((x) => x.r === 1 && x.c === 2)?.say, undefined);
});
