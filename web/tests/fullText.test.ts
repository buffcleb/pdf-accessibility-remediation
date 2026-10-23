// A tag's alt text and details can always be read in full (#436): the tree row's title and screen-reader text, and
// where the hover and focus card goes against the pane's edges. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagElement } from "../src/api";
import { CARD_MAX_H, placeCard, rowTitle, rowWords, tagText } from "../src/viewer/fullText";

const LONG = "A bar chart of enrolment by year from 2010 to 2025, rising from about 4,000 students to just over "
  + "7,500, with the steepest rise between 2019 and 2021 when the online programmes opened.";
const el = (over: Partial<TagElement>): TagElement =>
  ({ i: 3, role: "Figure", parent: 0, kids: [], boxes: [], own: true, order: 1, ...over });

test("a long alt text is cut on the row, and given in full to its title and to a screen reader", () => {
  const w = rowWords(el({ alt: LONG }));
  assert.ok(w.cut);
  assert.ok(w.short.endsWith("…") && w.short.length < LONG.length);
  assert.equal(w.full, `alt: ${LONG}`);  // the row's screen-reader text
  const title = rowTitle("Figure", w, ["Reading order: 4 on page 2"]);
  assert.deepEqual(title.split("\n"), ["Figure", `alt: ${LONG}`, "Reading order: 4 on page 2"]);
});

test("pending alt text is the one shown, and long read-aloud and paragraph text is given in full too", () => {
  assert.equal(rowWords(el({ alt: "Old" }), LONG).full, `alt: ${LONG}`);
  assert.equal(rowWords(el({ role: "Span", actual_text: LONG })).full, LONG);
  assert.equal(rowWords(el({ role: "P", text: LONG })).full, LONG);
  // The tag map cuts `text` short; `say` has the paragraph's own words in full
  const cut = LONG.slice(0, 120);
  assert.equal(tagText(el({ role: "P", text: cut, say: LONG })), LONG);
  assert.equal(rowWords(el({ role: "P", text: cut, say: LONG })).full, LONG);
  // Some of the words in a child tag (a link): `say` is only the rest, so the whole of `text` is shown
  assert.equal(tagText(el({ role: "P", text: LONG, say: "A bar chart" })), LONG);
});

test("short words aren't cut, and the title doesn't say them twice", () => {
  const w = rowWords(el({ alt: "A campus map" }));
  assert.deepEqual(w, { full: "alt: A campus map", short: "alt: A campus map", cut: false });
  assert.equal(rowTitle("Figure", w, ["No content of its own"]), "Figure\nNo content of its own");
});

// The pane's visible part, in the page's pixels: 0–600 across, 0–800 down
const VIEW = { left: 0, top: 0, right: 600, bottom: 800 };
const CARD = { w: 330, h: 200 };

test("the card goes above the tag's outline when there's room", () => {
  const p = placeCard({ left: 100, top: 400, right: 300, bottom: 450 }, CARD, VIEW);
  assert.equal(p.side, "above");
  assert.equal(p.top, 400 - 6 - 200);
  assert.equal(p.left, 100);
});

test("near the top edge it goes below the outline, not cut off above it", () => {
  const p = placeCard({ left: 100, top: 20, right: 300, bottom: 120 }, CARD, VIEW);
  assert.equal(p.side, "below");
  assert.equal(p.top, 126);
  // The pane scrolled down so the outline starts above what can be seen: still inside the pane
  const q = placeCard({ left: 100, top: -50, right: 300, bottom: 60 }, CARD, VIEW);
  assert.ok(q.top >= VIEW.top + 8);
});

test("near the bottom edge it stays above; a tall outline with no room either side keeps the card inside", () => {
  assert.equal(placeCard({ left: 100, top: 700, right: 300, bottom: 790 }, CARD, VIEW).side, "above");
  const p = placeCard({ left: 100, top: 100, right: 300, bottom: 760 }, CARD, VIEW);  // a page-high figure
  assert.ok(p.top >= VIEW.top + 8 && p.top + CARD.h <= VIEW.bottom - 8);
});

test("near the left and right edges it's moved in, not cut", () => {
  assert.equal(placeCard({ left: -40, top: 400, right: 60, bottom: 450 }, CARD, VIEW).left, 8);
  const p = placeCard({ left: 520, top: 400, right: 590, bottom: 450 }, CARD, VIEW);
  assert.equal(p.left, 600 - 8 - 330);
});

test("a narrow pane makes it narrower, and long text makes it scroll at a maximum height", () => {
  const narrow = placeCard({ left: 10, top: 400, right: 100, bottom: 450 }, CARD, { ...VIEW, right: 250 });
  assert.equal(narrow.width, 250 - 16);
  assert.equal(narrow.left, 8);
  const tall = placeCard({ left: 10, top: 400, right: 100, bottom: 450 }, { w: 330, h: 2000 }, VIEW);
  assert.equal(tall.maxHeight, CARD_MAX_H);
  assert.ok(tall.top >= 8 && tall.top + tall.maxHeight <= 800 - 8);
  const short = placeCard({ left: 10, top: 100, right: 100, bottom: 150 }, { w: 330, h: 2000 },
    { ...VIEW, bottom: 300 });
  assert.equal(short.maxHeight, 300 - 16);  // a short pane: no taller than it
  assert.equal(short.top, 8);
});

test("a tag scrolled out of sight takes its card with it", () => {
  assert.equal(placeCard({ left: 10, top: -300, right: 100, bottom: -200 }, CARD, VIEW).side, "off");
  assert.equal(placeCard({ left: 10, top: 900, right: 100, bottom: 950 }, CARD, VIEW).side, "off");
});
