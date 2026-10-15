// Splitting a figure that holds several drawings. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap, TagPiece } from "../src/api";
import { bySize, figureEdits, figureFrame, figurePieces, groupProblem, pieceAt, pieceKey, piecesIn, splitPreview } from "../src/viewer/figure";
import { tagMenu } from "../src/viewer/menu";
import { describe, editMarks, pendingArea } from "../src/viewer/draft";

const piece = (mcid: number, bbox: [number, number, number, number], kind: TagPiece["kind"] = "drawing", text?: string, page = 1): TagPiece =>
  ({ page, mcid, el: 1, bbox, kind, ...(text ? { text } : {}) });
// A scheme: two ring drawings, each with a label, and an arrow between them
const pieces = [piece(1, [100, 100, 160, 160]), piece(2, [165, 120, 180, 132], "text", "OH"), piece(3, [190, 125, 240, 135]),
  piece(4, [250, 100, 310, 160]), piece(5, [312, 120, 330, 132], "text", "NH2")];
const fig = { i: 1, parent: 0, kids: [], role: "Figure", own: true, order: 0, page: 1, alt: "A two-step scheme", boxes: [{ page: 1, bbox: [100, 100, 330, 160] }] } as TagElement;
const map: TagMap = { schema: 1, pages: [{ w: 600, h: 800 }], pieces, elements: [{ i: 0, parent: null, kids: [1], role: "Document", own: false, order: null, boxes: [] } as TagElement, fig] };

test("a figure's pieces and the part of the page they cover", () => {
  assert.equal(figurePieces(map, fig).length, 5);
  assert.deepEqual(figureFrame(pieces, 600, 800), [94, 94, 336, 166]);
  assert.equal(figurePieces({ ...map, pieces: [...pieces, piece(9, [0, 0, 5, 5], "drawing", undefined, 2)] }, fig).length, 5);  // its first page only
});

test("groups of pieces become new tags, the rest stays, and its alt text can be redone", () => {
  const keys = (...m: number[]) => m.map((n) => pieceKey(pieces[n - 1]));
  assert.ok(/Click the pieces/.test(groupProblem(pieces, new Set(), new Set(), "Figure", "x")!));
  assert.ok(/needs alt text/.test(groupProblem(pieces, new Set(), new Set(keys(4, 5)), "Figure", "")!));
  assert.ok(/Leave at least one/.test(groupProblem(pieces, new Set(keys(1, 2, 3)), new Set(keys(4, 5)), "Figure", "x")!));
  assert.equal(groupProblem(pieces, new Set(), new Set(keys(4, 5)), "Figure", "The product"), null);
  const edits = figureEdits(fig, pieces, [{ keys: keys(4, 5), role: "Figure", alt: "The product" }, { keys: keys(3), role: "Caption", alt: "" }], "The starting ring");
  assert.deepEqual(edits, [
    { op: "take_pieces", element: 1, expect_role: "Figure", role: "Caption", pieces: [{ page: 1, mcid: 3 }] },
    { op: "take_pieces", element: 1, expect_role: "Figure", role: "Figure", pieces: [{ page: 1, mcid: 4 }, { page: 1, mcid: 5 }], alt: "The product" },
    { op: "set_alt", element: 1, expect_role: "Figure", alt: "The starting ring" },
  ]);
  assert.deepEqual(figureEdits(fig, pieces, [], "A two-step scheme"), []);                 // nothing changed
  assert.ok(/2 pieces of Figure.* made into a new Figure/.test(describe(edits[1], map)));
  assert.deepEqual(editMarks(map, [edits[1]]).get(1), ["split: new Figure"]);
  assert.equal(pendingArea([edits[1]]).pieces.get("1:4"), -1);                             // not offered again
});

test("the menu offers to split a figure of several pieces, once per save", () => {
  const item = (ops = [] as Parameters<typeof tagMenu>[1]) => tagMenu(map, ops, [1]).find((x) => x.id === "figure-split");
  assert.equal(item()!.act, "figure-split");
  assert.equal(tagMenu({ ...map, pieces: pieces.slice(0, 1) }, [], [1]).find((x) => x.id === "figure-split")!.act, "figure-split");  // one piece: cut a box
  assert.equal(tagMenu({ ...map, pieces: [] }, [], [1]).find((x) => x.id === "figure-split"), undefined);
  assert.ok(/already being split/.test(item([{ op: "take_pieces", element: 1, expect_role: "Figure", role: "Figure", pieces: [{ page: 1, mcid: 4 }], alt: "x" }])!.disabled!));
});

test("a click picks the smallest piece under it, so pieces inside bigger ones can be picked", () => {
  // a big drawing with a small label inside it, as an atom label sits inside a ring drawing's box
  const big = piece(10, [100, 100, 300, 300]), small = piece(11, [150, 150, 170, 162], "text", "OH");
  const all = [big, small];
  assert.equal(pieceAt(all, 160, 156), pieceKey(small));                     // on the label: the label
  assert.equal(pieceAt(all, 250, 250), pieceKey(big));                       // elsewhere in the drawing: the drawing
  assert.equal(pieceAt(all, 160, 156, new Set([pieceKey(small)])), pieceKey(big));   // the label taken already
  assert.equal(pieceAt(all, 20, 20), null);
  assert.deepEqual(bySize(all).map(pieceKey), [pieceKey(big), pieceKey(small)]);   // the label's outline drawn on top
});

test("a box drawn over the figure picks the pieces wholly inside it", () => {
  assert.deepEqual(piecesIn(pieces, [240, 90, 340, 170]), [pieceKey(pieces[3]), pieceKey(pieces[4])]);   // the second ring and its label
  assert.deepEqual(piecesIn(pieces, [340, 170, 240, 90]), [pieceKey(pieces[3]), pieceKey(pieces[4])]);   // dragged up and left
  assert.deepEqual(piecesIn(pieces, [240, 90, 320, 170]), [pieceKey(pieces[3])]);                         // the label sticks out
  assert.deepEqual(piecesIn(pieces, [240, 90, 340, 170], new Set([pieceKey(pieces[4])])), [pieceKey(pieces[3])]);
});

test("a box cut out of a figure is a cut_area edit, after any whole pieces taken", () => {
  const box: [number, number, number, number] = [240, 90, 340, 170];
  assert.equal(groupProblem(pieces, new Set(), new Set(), "Figure", "", true)!.includes("needs alt text"), true);
  assert.equal(groupProblem(pieces, new Set(), new Set(), "Caption", "", true), null);
  const edits = figureEdits(fig, pieces, [
    { keys: [], role: "Figure", alt: "The product", box },
    { keys: [pieceKey(pieces[2])], role: "Caption", alt: "" },
  ], "A two-step scheme");
  assert.deepEqual(edits, [
    { op: "take_pieces", element: 1, expect_role: "Figure", role: "Caption", pieces: [{ page: 1, mcid: 3 }] },
    { op: "cut_area", element: 1, expect_role: "Figure", role: "Figure", page: 1, bbox: box, alt: "The product" },
  ]);
  assert.ok(/cut out of Figure.* into a new Figure/.test(describe(edits[1], map)));
});

test("the tags a pending split will make are drawn after the figure, in the order they'll be read", () => {
  const box: [number, number, number, number] = [240, 90, 340, 170];
  const edits = figureEdits(fig, pieces, [
    { keys: [pieceKey(pieces[0]), pieceKey(pieces[1])], role: "Figure", alt: "The starting ring" },
    { keys: [], role: "Figure", alt: "The product", box },
  ], "A two-step scheme");
  const parts = splitPreview(map, edits).get(1)!;
  // As the engine will read them: whole pieces are taken before boxes are cut, and each new tag goes right after the
  // figure, so here the box (cut last) is read first
  assert.deepEqual(parts.map((p) => p.alt), ["The product", "The starting ring"]);
  assert.deepEqual(parts[1].boxes, [{ page: 1, bbox: [100, 100, 180, 160] }]);            // round the pieces taken
  assert.deepEqual(parts[0].boxes, [{ page: 1, bbox: box }]);                               // the box drawn
  // groups of one kind are read in the order they were made
  const same = figureEdits(fig, pieces, [{ keys: [pieceKey(pieces[0])], role: "Figure", alt: "One" }, { keys: [pieceKey(pieces[3])], role: "Figure", alt: "Two" }], "x");
  assert.deepEqual(splitPreview(map, same).get(1)!.map((p) => p.alt), ["One", "Two"]);
  assert.equal(splitPreview(map, []).size, 0);
});
