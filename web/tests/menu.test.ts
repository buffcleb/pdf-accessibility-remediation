// The tag editor's menu and selecting several tags. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagEdit, TagElement, TagMap } from "../src/api";
import { menuAndTypes, menuEntries, pageMenu, pick, pointsAt, tagMenu } from "../src/viewer/menu";
import { splitPieces } from "../src/viewer/pieces";
import { describe, editMarks, listPlan, overrides, withoutEdits } from "../src/viewer/draft";

type Spec = Partial<TagElement> & { i: number; role: string; parent: number | null };

function doc(specs: Spec[]): TagMap {
  let order = 0;
  const elements = specs.map((s): TagElement => ({
    kids: specs.filter((k) => k.parent === s.i).map((k) => k.i),
    boxes: s.own ? [{ page: s.page ?? 1, bbox: [0, 100 * s.i, 200, 100 * s.i + 20] }] : [],
    own: false, page: 1, ...s, order: s.own ? order++ : null,
  } as TagElement));
  return { schema: 1, pages: [{ w: 600, h: 800 }], elements };
}

const sample = () => doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "H1", parent: 0, own: true, text: "Title" },
  { i: 2, role: "P", parent: 0, own: true, text: "First point" },
  { i: 3, role: "P", parent: 0, own: true, text: "Second point" },
  { i: 4, role: "Figure", parent: 0, own: true, alt: "A chart" },
  { i: 5, role: "Sect", parent: 0 },
  { i: 6, role: "P", parent: 5, own: true, text: "Inside a section",
    parts: [{ k: 0, kind: "content", page: 1, bbox: [0, 600, 200, 610], text: "Inside" },
      { k: 1, kind: "content", page: 1, bbox: [0, 612, 200, 622], text: "a section" }] },
]);

const item = (items: ReturnType<typeof tagMenu>, id: string) => items.find((x) => x.id === id);

test("one tag: its type can be changed, and its own type isn't offered", () => {
  const items = tagMenu(sample(), [], [2]);
  assert.equal(item(items, "role:P"), undefined);
  assert.deepEqual(item(items, "role:H2")!.ops, [{ op: "set_role", element: 2, expect_role: "P", role: "H2" }]);
  assert.ok(item(items, "role:L")!.note!.includes("list"));     // what else it does is said
  assert.equal(item(items, "merge"), undefined);                 // nothing to merge with
});

test("several tags: one change of type for all of them, and one merge in reading order", () => {
  const map = sample();
  const items = tagMenu(map, [], [3, 2]);   // chosen out of order
  assert.deepEqual(item(items, "role:H3")!.ops!.map((o) => ("element" in o ? o.element : null)), [2, 3]);
  assert.deepEqual(item(items, "merge")!.ops, [{ op: "merge", element: 2, expect_role: "P", absorb: [3] }]);
  assert.equal(item(items, "split-text"), undefined);            // splitting is for one tag
  assert.equal(item(items, "earlier"), undefined);
  // A merge already under way is added to, not started again
  const more = tagMenu(map, item(items, "merge")!.ops!, [2, 6]);
  assert.deepEqual(more.find((x) => x.id === "merge")!.ops, [{ op: "merge", element: 2, expect_role: "P", absorb: [3, 6] }]);
});

test("a choice that would break the tags is shown, refused, with the reason", () => {
  const map = sample();
  const merge = item(tagMenu(map, [], [5, 6]), "merge")!;        // a section and the paragraph inside it
  assert.ok(merge.disabled && merge.ops === undefined);
  const group = item(tagMenu(map, [], [5, 2]), "merge")!;
  assert.ok(group.disabled!.includes("groups"));
  assert.ok(item(tagMenu(map, [], [1]), "earlier")!.disabled!.includes("already first"));
  assert.ok(tagMenu(map, [], [2], null, false).filter((x) => x.ops || x.act === "delete").every((x) => x.disabled));
});

test("split here is offered where the menu was opened, when that is a later piece of the tag", () => {
  const map = sample();
  const second = tagMenu(map, [], [6], { page: 1, x: 50, y: 615 });
  assert.deepEqual(item(second, "split-here")!.ops, [{ op: "split", element: 6, expect_role: "P", at: 1 }]);
  assert.equal(item(second, "split-text")!.label, "Split somewhere else…");
  const first = tagMenu(map, [], [6], { page: 1, x: 50, y: 605 });   // on the first piece: nothing before it
  assert.equal(item(first, "split-here"), undefined);
  assert.equal(item(first, "split-text")!.label, "Split…");
  assert.equal(item(tagMenu(map, [], [2], { page: 1, x: 5, y: 205 }), "split-here"), undefined);   // one piece only
});

test("moving in the reading order, decorative, and undoing a tag's own changes", () => {
  const map = sample();
  const items = tagMenu(map, [], [3]);
  assert.deepEqual(item(items, "earlier")!.ops, [{ op: "move", element: 3, expect_role: "P", target: 2, expect_target_role: "P", position: "before" }]);
  assert.ok(item(items, "later")!.ops);
  assert.ok(item(tagMenu(map, [], [4]), "decorative")!.ops);
  assert.equal(item(items, "decorative"), undefined);
  const ops = [{ op: "set_role" as const, element: 3, expect_role: "P", role: "H2" }, { op: "set_role" as const, element: 2, expect_role: "P", role: "H2" }];
  assert.deepEqual(item(tagMenu(map, ops, [3]), "undo")!.ops, [ops[1]]);
  assert.equal(item(items, "undo"), undefined);                  // nothing of its own to undo
});

test("clicking with Ctrl adds and takes away; with Shift takes the run between", () => {
  const visible = [1, 2, 3, 4, 6];
  assert.deepEqual(pick([], 3, "toggle"), [3]);
  assert.deepEqual(pick([2], 4, "toggle"), [2, 4]);
  assert.deepEqual(pick([2, 4], 2, "toggle"), [4]);
  assert.deepEqual(pick([4], 4, "toggle"), [4]);                 // the last one stays selected
  assert.deepEqual(pick([2], 4, "range", visible), [2, 3, 4]);
  assert.deepEqual(pick([4], 2, "range", visible), [4, 3, 2]);   // the first chosen stays first
  assert.deepEqual(pick([2, 6], 3, "one"), [3]);
  assert.deepEqual(pick([2], 9, "range", visible), [2, 9]);      // not in view: just added
});

// --- lists -------------------------------------------------------------------------------------------

// Document > P(1) P(2) P(3) Figure(4) L(5) > [LI(6) > LBody(7) > [P(8), L(9) > LI(10) > LBody(11), LI(12) > LBody(13)]], LI(14) > LBody(15)
const listed = () => doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "P", parent: 0, own: true, text: "Point one" },
  { i: 2, role: "P", parent: 0, own: true, text: "Point two" },
  { i: 3, role: "P", parent: 0, own: true, text: "Point three" },
  { i: 4, role: "Figure", parent: 0, own: true, alt: "A chart" },
  { i: 5, role: "L", parent: 0 },
  { i: 6, role: "LI", parent: 5 },
  { i: 7, role: "LBody", parent: 6, own: true, text: "First" },
  { i: 8, role: "L", parent: 7 },
  { i: 9, role: "LI", parent: 8 },
  { i: 10, role: "LBody", parent: 9, own: true, text: "First, a" },
  { i: 11, role: "LI", parent: 8 },
  { i: 12, role: "LBody", parent: 11, own: true, text: "First, b" },
  { i: 13, role: "LI", parent: 5 },
  { i: 14, role: "LBody", parent: 13, own: true, text: "Second" },
]);

test("several tags: one list can be made of them, in reading order", () => {
  const items = tagMenu(listed(), [], [3, 1, 2]);
  assert.deepEqual(item(items, "make-list")!.ops, [{ op: "make_list", element: 1, expect_role: "P", absorb: [2, 3] }]);
  // a figure would lose its description as a list item
  assert.ok(/can't become a list item/.test(item(tagMenu(listed(), [], [1, 4]), "make-list")!.disabled!));
  // a paragraph and a list: the list gives its items
  const mixed = item(tagMenu(listed(), [], [3, 5]), "make-list")!;
  assert.equal(mixed.disabled, undefined);
  assert.ok(/gives its items/.test(mixed.note!));
  // a tag can't be put in two new lists
  const pending = item(items, "make-list")!.ops!;
  assert.ok(/already becoming part of a list/.test(item(tagMenu(listed(), pending, [2, 3]), "make-list")!.disabled!));
});

test("list levels: items of a new list can be indented before saving, the first can't", () => {
  const map = listed();
  const made = item(tagMenu(map, [], [1, 2, 3]), "make-list")!.ops!;
  assert.equal(item(tagMenu(map, [], [2]), "indent"), undefined);          // a paragraph isn't an item yet
  assert.ok(/no item before it/.test(item(tagMenu(map, made, [1]), "indent")!.disabled!));
  const both = item(tagMenu(map, made, [2, 3]), "indent")!;
  assert.deepEqual(both.ops!.slice(1), [{ op: "indent", element: 2, expect_role: "P" }, { op: "indent", element: 3, expect_role: "P" }]);
  const plan = listPlan(map, both.ops!);
  assert.deepEqual([1, 2, 3].map(plan.level), [1, 2, 2]);
  assert.equal(plan.bad.size, 0);
  assert.deepEqual(editMarks(map, both.ops!).get(3), ["list item", "list level 2"]);
  assert.ok(/One list made of 3 tags/.test(describe(both.ops![0], map)));
  assert.ok(/already at the top level/.test(item(tagMenu(map, made, [2]), "outdent")!.disabled!));
});

test("list levels: in a list the file has, the text of an item stands for the item", () => {
  const map = listed();
  // the body of the second top-level item is what a click on the page selects
  assert.deepEqual(item(tagMenu(map, [], [14]), "indent")!.ops, [{ op: "indent", element: 13, expect_role: "LI" }]);
  assert.equal(listPlan(map, item(tagMenu(map, [], [14]), "indent")!.ops!).level(13), 2);   // joins the nested list
  // outdenting the first nested item takes the one after it along, under it
  const out = item(tagMenu(map, [], [9]), "outdent")!.ops!;
  const plan = listPlan(map, out);
  assert.deepEqual([6, 9, 11, 13].map(plan.level), [1, 1, 2, 1]);
  assert.equal(item(tagMenu(map, [], [5]), "indent"), undefined);          // the list itself isn't an item
});

test("a list becomes a table of contents: offered from the list or any tag in it, for the outermost list", () => {
  const map = listed();
  const toc = [{ op: "make_toc", element: 5, expect_role: "L" }];
  assert.deepEqual(item(tagMenu(map, [], [5]), "toc")!.ops, toc);
  assert.equal(item(tagMenu(map, [], [5]), "toc")!.label, "Make it a table of contents");
  assert.deepEqual(item(tagMenu(map, [], [10]), "toc")!.ops, toc);        // the text of a nested item: the outer list
  assert.equal(item(tagMenu(map, [], [10]), "toc")!.label, "Make the list a table of contents");
  assert.equal(item(tagMenu(map, [], [2]), "toc"), undefined);            // a paragraph outside any list
  // Before saving, the tree shows the new types; choosing it again keeps it a list
  assert.deepEqual([5, 6, 7, 8, 9, 10].map((i) => overrides(toc as TagEdit[], map).get(i)?.role),
    ["TOC", "TOCI", "Reference", "TOC", "TOCI", "Reference"]);
  assert.deepEqual(editMarks(map, toc as TagEdit[]).get(5), ["table of contents"]);
  assert.ok(/made into a table of contents/.test(describe(toc[0] as TagEdit, map)));
  const undo = item(tagMenu(map, toc as TagEdit[], [14]), "toc")!;
  assert.deepEqual([undo.label, undo.ops], ["Keep it a list", []]);
  // Its levels are set before, not after; and a list being reshaped waits for that to be saved
  assert.ok(/becoming a table of contents/.test(item(tagMenu(map, toc as TagEdit[], [13]), "indent")!.disabled!));
  const moved = [{ op: "set_role", element: 7, expect_role: "LBody", role: "P" }] as TagEdit[];
  assert.ok(/unsaved changes/.test(item(tagMenu(map, moved, [5]), "toc")!.disabled!));
});

test("a list whose item holds a table can't become a table of contents", () => {
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "L", parent: 0 },
    { i: 2, role: "LI", parent: 1 },
    { i: 3, role: "LBody", parent: 2, own: true, text: "Rates" },
    { i: 4, role: "Table", parent: 3 },
  ]);
  assert.ok(/holds Table/.test(item(tagMenu(map, [], [1]), "toc")!.disabled!));
});

test("a tag can be pointed at a footnote on its page or the next, and unpointed", () => {
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "P", parent: 0, own: true, text: "Body text with a mark", page: 1 },
    { i: 2, role: "Note", parent: 0, own: true, text: "1 Smith and Jones", page: 1 },
    { i: 3, role: "Note", parent: 0, own: true, text: "2 Later note", page: 2 },
    { i: 4, role: "Note", parent: 0, own: true, text: "9 Far away", page: 5 },
    { i: 5, role: "P", parent: 0, own: true, text: "1 Read as a paragraph", page: 1 },
  ]);
  const notes = tagMenu(map, [], [1]).filter((x) => x.group === "note");
  assert.deepEqual(notes.map((x) => x.id), ["note:2", "note:3"]);           // the same page first; not five pages on
  assert.ok(notes[1].label.includes("(page 2)"));
  const linked = notes[0].ops!;
  assert.deepEqual(linked, [{ op: "link_note", element: 1, expect_role: "P", note: 2 }]);
  assert.equal(pointsAt(map, linked, 1), 2);
  assert.ok(/already points/.test(item(tagMenu(map, linked, [1]), "note:2")!.disabled!));
  assert.deepEqual(item(tagMenu(map, linked, [1]), "note:none")!.ops, [{ op: "link_note", element: 1, expect_role: "P", note: null }]);
  assert.ok(/points at the footnote “1 Smith/.test(describe(linked[0], map)));
  assert.deepEqual(editMarks(map, linked).get(1), ["points at a footnote"]);
  // A paragraph made a note before saving can be pointed at; a note itself points at nothing
  const made: TagEdit[] = [{ op: "set_role", element: 5, expect_role: "P", role: "Note" }];
  assert.ok(item(tagMenu(map, made, [1]), "note:5"));
  assert.equal(tagMenu(map, [], [2]).filter((x) => x.group === "note").length, 0);
});

test("undoing a tag's changes keeps the rest workable", () => {
  const map = listed();
  const ops = item(tagMenu(map, item(tagMenu(map, [], [1, 2, 3]), "make-list")!.ops!, [2, 3]), "indent")!.ops!;
  // taking the second tag back out: the third has the first to go under
  assert.deepEqual(withoutEdits(map, ops, [2]), [{ op: "make_list", element: 1, expect_role: "P", absorb: [3] }, { op: "indent", element: 3, expect_role: "P" }]);
  // taking the first out undoes the list, and the indents with it
  assert.deepEqual(withoutEdits(map, ops, [1]), []);
  assert.ok(item(tagMenu(map, ops, [3]), "undo"));                          // offered on a tag that only joined
});

test("the types are behind one line of the menu, and go to their own dialog", () => {
  const { menu, types } = menuAndTypes(tagMenu(sample(), [], [2, 3]));
  assert.equal(menu.filter((i) => i.group === "type").length, 1);
  assert.deepEqual([menu[0].id, menu[0].act, menu[0].disabled], ["types", "types", undefined]);
  assert.ok(types.length > 5 && types.every((t) => t.id.startsWith("role:")));
  assert.ok(menu.some((i) => i.id === "merge"));                           // the rest of the menu is as it was
  // a version that can't be edited: the one line says why, as every type would
  const locked = menuAndTypes(tagMenu(sample(), [], [2], null, false)).menu[0];
  assert.ok(/can't be edited/.test(locked.disabled!));
});

test("a page with no tag under the pointer: the area tools", () => {
  const items = pageMenu(3, false, false);
  assert.deepEqual(items.map((i) => [i.id, i.act, i.disabled]), [["area-draw", "area-draw", undefined], ["area-page", "area-page", undefined]]);
  assert.equal(items[1].label, "Use all of page 3");
  assert.equal(pageMenu(3, true, false).at(-1)!.id, "area-clear");          // only when there is an area
  assert.equal(pageMenu(3, false, true)[0].label, "Stop choosing an area");
  assert.ok(pageMenu(3, false, false, false).every((i) => /can't be edited/.test(i.disabled!)));
});

test("the menu's top level is short: kinds of change open submenus, one-choice kinds and More stay on it", () => {
  const { menu } = menuAndTypes(tagMenu(sample(), [], [6], { page: 1, x: 10, y: 615 }));
  const sections = menuEntries(menu);
  const top = sections.map((s) => s.map((e) => (e.kind === "item" ? e.item.id : `${e.label} >`)));
  assert.deepEqual(top[0], ["types", "Merge and split >", "Lists and tables >", "Reading order >", "Mark as artifact >"]);
  assert.deepEqual(top[top.length - 1], ["edit", "delete", "read", "report"]);         // More: on the top level
  const order = sections[0].find((e) => e.kind === "group" && e.group === "order");
  assert.ok(order && order.kind === "group" && order.items.length === 2);
  // a kind where nothing can be chosen now still opens, dimmed, to say why
  const locked = menuEntries(menuAndTypes(tagMenu(sample(), [], [6], { page: 1, x: 10, y: 615 }, false)).menu)[0];
  assert.ok(locked.some((e) => e.kind === "group" && e.none));
  // the page menu's area tools are a plain list
  assert.deepEqual(menuEntries(pageMenu(2, false, false)).map((s) => s.map((e) => e.kind)), [["item", "item"]]);
});

test("one tag can be opened in the edit dialog; a version that can't be edited still shows its details", () => {
  assert.deepEqual([item(tagMenu(sample(), [], [2]), "edit")!.label, item(tagMenu(sample(), [], [2]), "edit")!.act], ["Edit tag…", "edit"]);
  assert.equal(item(tagMenu(sample(), [], [2], null, false), "edit")!.label, "Tag details…");
  assert.equal(item(tagMenu(sample(), [], [2], null, false), "edit")!.disabled, undefined);
  assert.equal(item(tagMenu(sample(), [], [2, 3]), "edit"), undefined);            // several tags: one at a time
});

test("the split window offers a tag's pieces of text, not its pictures", () => {
  const el = sample().elements[6];
  assert.deepEqual(splitPieces(el).map((p) => p.k), [0, 1]);
  assert.deepEqual(splitPieces({ ...el, parts: [{ k: 0, kind: "content", text: "(image)" }, { k: 1, kind: "content", text: "Caption words" }] }).map((p) => p.k), [1]);
  assert.deepEqual(splitPieces({ ...el, parts: undefined, own: true, kids: [], text: "Whole text" }), [{ k: 0, text: "Whole text" }]);
  assert.ok(/window/.test(item(tagMenu(sample(), [], [6]), "split-text")!.note!));
});

test("any text or figure tag can be made an artifact of a kind, but not one holding a link", () => {
  const map = sample();
  const p = tagMenu(map, [], [3]);
  assert.deepEqual(item(p, "artifact-footer")!.ops, [{ op: "set_decorative", element: 3, expect_role: "P", as: "footer" }]);
  assert.deepEqual(item(p, "artifact-layout")!.ops, [{ op: "set_decorative", element: 3, expect_role: "P" }]);
  assert.equal(item(p, "artifact-layout")!.group, "artifact");
  // A figure's plain artifact is still "Decorative"; it can be a running header's logo too
  const fig = tagMenu(map, [], [4]);
  assert.equal(item(fig, "decorative")!.label, "Decorative");
  assert.ok(item(fig, "artifact-header")!.ops);
  // The document's top tag stays; a tag holding a link has to stay readable
  assert.equal(item(tagMenu(map, [], [0]), "artifact-layout"), undefined);
  const linked = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "P", parent: 0, own: true, text: "See the site" },
    { i: 2, role: "Link", parent: 1, annot: true },
  ]);
  assert.ok(/link or form field/.test(item(tagMenu(linked, [], [1]), "artifact-footer")!.disabled ?? ""));
});

test("the table editor opens from a table or any of its cells, and text tags can be made a table", () => {
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "P", parent: 0, own: true, text: "Lot Price" },
    { i: 2, role: "P", parent: 0, own: true, text: "North $145" },
    { i: 3, role: "Table", parent: 0 },
    { i: 4, role: "TR", parent: 3 },
    { i: 5, role: "TD", parent: 4, own: true, text: "Q1" },
  ]);
  assert.equal(item(tagMenu(map, [], [5]), "table")!.act, "table");
  assert.equal(item(tagMenu(map, [], [3]), "table")!.disabled, undefined);
  assert.equal(item(tagMenu(map, [], [1]), "table"), undefined);
  // Other unsaved changes inside it first
  const pending = [{ op: "set_cell" as const, element: 5, expect_role: "TD", role: "TH" as const, scope: "Column" }];
  assert.ok(/other unsaved changes/.test(item(tagMenu(map, pending, [3]), "table")!.disabled ?? ""));
  // Two paragraphs: enough pieces for a table
  const make = item(tagMenu(map, [], [1, 2]), "make-table")!;
  assert.deepEqual([make.act, make.group, make.disabled], ["make-table", "list", undefined]);
  assert.equal(item(tagMenu(map, [], [1]), "make-table"), undefined);  // one piece isn't a table
  // A figure that is really a drawn table can be made one too (its alt text goes)
  const fig = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "Figure", parent: 0, own: true, alt: "A grid", parts: [{ k: 0, kind: "content" }, { k: 1, kind: "content" }] },
  ]);
  const f = item(tagMenu(fig, [], [1]), "make-table")!;
  assert.equal(f.disabled, undefined);
  assert.ok(/alt text goes/.test(f.note ?? ""));
});
