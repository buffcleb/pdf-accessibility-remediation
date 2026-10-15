// What "Read aloud" says for a small tagged document. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement, TagMap } from "../src/api";
import { chunks, speechScript, startFor } from "../src/viewer/speech";

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
  // Groups get the boxes of their content, as in a real tag map
  for (const e of [...elements].reverse()) {
    if (e.parent !== null) elements[e.parent].boxes.push(...e.boxes);
  }
  return { schema: 1, pages: [{ w: 600, h: 800 }, { w: 600, h: 800 }], elements };
}

const sample = () => doc([
  { i: 0, role: "Document", parent: null },
  { i: 1, role: "H1", parent: 0, own: true, text: "Annual report" },
  { i: 2, role: "P", parent: 0, own: true, text: "Short version", say: "Short version of a long paragraph." },
  { i: 3, role: "Figure", parent: 0, own: true, alt: "Bar chart of sales", text: "Q1 Q2" },
  { i: 4, role: "L", parent: 0 },
  { i: 5, role: "LI", parent: 4 },
  { i: 6, role: "LBody", parent: 5, own: true, text: "First" },
  { i: 7, role: "LI", parent: 4 },
  { i: 8, role: "LBody", parent: 7, own: true, text: "Second" },
  { i: 9, role: "Table", parent: 0 },
  { i: 10, role: "TR", parent: 9 },
  { i: 11, role: "TH", parent: 10, own: true, text: "Quarter" },
  { i: 12, role: "TD", parent: 10, own: true, text: "Q1" },
  { i: 13, role: "P", parent: 0, own: true, text: "Bonjour", lang: "fr-CA", page: 2 },
  { i: 14, role: "P", parent: 0, own: true, text: "x 2", actual_text: "x squared", page: 2 },
]);

test("reads tags in order, with roles, alt and read-aloud text in place of content", () => {
  const said = speechScript(sample()).map((u) => u.text);
  assert.deepEqual(said, [
    "Heading level 1, Annual report",
    "Short version of a long paragraph.",
    "Figure: Bar chart of sales",
    "List, 2 items", "First", "Second", "End of list",
    "Table, 1 row, 2 columns", "Header: Quarter", "Q1", "End of table",
    "Bonjour",
    "x squared",
  ]);
});

test("a tag's language goes with what's read, and each item knows its page", () => {
  const script = speechScript(sample());
  const fr = script.find((u) => u.text === "Bonjour")!;
  assert.equal(fr.lang, "fr-CA");
  assert.equal(fr.page, 2);
  assert.equal(script[0].lang, undefined);
});

test("content inside a tag with alt text is read once, as the alt text", () => {
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "Figure", parent: 0, alt: "Org chart" },
    { i: 2, role: "P", parent: 1, own: true, text: "CEO" },
    { i: 3, role: "P", parent: 1, own: true, text: "CFO" },
  ]);
  assert.deepEqual(speechScript(map).map((u) => u.text), ["Figure: Org chart"]);
});

test("pending edits are heard: a move, a new role, new alt text, decorative and a merge", () => {
  const map = sample();
  const said = speechScript(map, [
    { op: "move", element: 2, expect_role: "P", target: 1, expect_target_role: "H1", position: "before" },
    { op: "set_role", element: 1, expect_role: "H1", role: "H2" },
    { op: "set_alt", element: 3, expect_role: "Figure", alt: "Sales rose" },
    { op: "set_decorative", element: 13, expect_role: "P" },
    { op: "merge", element: 6, expect_role: "LBody", absorb: [8] },
  ]).map((u) => u.text);
  assert.deepEqual(said.slice(0, 5), [
    "Short version of a long paragraph.",
    "Heading level 2, Annual report",
    "Figure: Sales rose",
    "List, 2 items",
    "First Second",
  ]);
  assert.ok(!said.includes("Bonjour"), "a decorative tag isn't read");
});

test("long text is split at sentence ends", () => {
  const text = "One sentence here. ".repeat(30).trim();
  const parts = chunks(text, 100);
  assert.ok(parts.length > 1);
  assert.ok(parts.every((p) => p.length <= 100 && p.endsWith(".")));
  assert.equal(parts.join(" "), text);
});

test("a form field is read by its name, in its place", () => {
  const map = doc([
    { i: 0, role: "Document", parent: null },
    { i: 1, role: "P", parent: 0, own: true, text: "Account number" },
    { i: 2, role: "Form", parent: 0, own: true, name: "Enter account number" },
    { i: 3, role: "Form", parent: 0, own: true },
  ]);
  assert.deepEqual(speechScript(map).map((u) => u.text),
    ["Account number", "Form field: Enter account number", "Form field: no name"]);
});

test("reading from a tag starts at its words, or at the first words inside it", () => {
  const map = sample();
  const script = speechScript(map);
  const at = (el: number) => { const n = startFor(script, map, el); return n < 0 ? null : script[n].el; };
  assert.equal(at(13), 13);                 // a paragraph: its own words, not the start of the page or document
  assert.equal(at(4), 4);                   // a list: where it's announced ("List, 2 items"), not where it ends
  assert.ok(/^List/.test(script[startFor(script, map, 4)].text));
  assert.equal(at(7), 8);                   // a list item, with no words of its own: its body
  assert.ok(startFor(script, map, 13) > 0);
});
