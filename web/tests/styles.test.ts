// A layout class means one thing. The viewer's heading was given a class the pane's own header already had; the
// older rule, later in the file, won, and the title sat at the left instead of the middle. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync("src/styles.css", "utf8");   // npm test runs in web/

/** How many rules set the layout of exactly this class (".name {", not ".name-more" or ".name .child"). */
function layouts(name: string): number {
  const rules = css.match(new RegExp(`(^|[\\n}])\\s*\\.${name}\\s*\\{[^}]*\\}`, "g")) ?? [];
  return rules.filter((r) => /display\s*:/.test(r)).length;
}

test("the viewer's heading and the pane's header are laid out by one rule each", () => {
  assert.equal(layouts("pv-top"), 1);
  assert.equal(layouts("pv-head"), 1);
});

test("the viewer's heading uses its own class", () => {
  const viewer = readFileSync("src/viewer/TagViewer.tsx", "utf8");
  assert.ok(viewer.includes('className="pv-top"'));
  assert.ok(!/className="pv-head"/.test(viewer));
});

test("the tag tree's helper text is folded away behind a twistie, and opens on a line of its own", () => {
  const tree = readFileSync("src/viewer/TreePane.tsx", "utf8");
  assert.ok(/<details className="pv-tree-help[^"]*"/.test(tree));
  assert.ok(!/<span className="small muted">\{show === "tree" \? "Tree"/.test(tree));     // no longer always shown
  assert.ok(/\.pv-tree-help\[open\]\s*\{[^}]*flex-basis:\s*100%/.test(css));
});

test("on a wide, tall window the viewer fits the window and its panes scroll inside themselves", () => {
  const block = css.match(/@media \(min-width: 1201px\) and \(min-height: 600px\) \{([\s\S]*?)\n\}/)?.[1] ?? "";
  assert.ok(/main\.viewer \{[^}]*height: calc\(100vh - var\(--topbar-h\)\)[^}]*overflow: hidden/.test(block));
  assert.ok(/main\.viewer > \.pv-grid \{[^}]*flex: 1 1 0[^}]*min-height: 0/.test(block));
  // The panel was sticky with an offset: kept as a plain box, the offset would push it below the window
  assert.ok(/main\.viewer \.pv-side \{[^}]*position: relative; top: auto/.test(block));
});

test("the top of each pane and the editing panel's tabs stay in view", () => {
  const flat = css.replace(/\s+/g, " ");
  assert.ok(/\.pv-head \{ position: sticky; top: 0; z-index: 12;/.test(flat));
  assert.ok(/@media \(min-height: 480px\) \{ \.pv-head \{ top: calc\(var\(--topbar-h\) \+ var\(--pv-top-h, 0px\)\); \} \}/.test(flat));
  assert.ok(/\.pv-side \.pv-tabs \{ position: sticky;/.test(flat));
  // Narrow, the panel is under the panes and doesn't scroll by itself: as a scroll box it would hold its tabs back
  assert.ok(/max-width: 1200px[^@]*\.pv-side \{[^}]*overflow: visible !important/.test(flat));
  // The viewer's box can't be scrolled by the page itself, which would slide the panes' tops out of sight
  assert.ok(/main\.viewer \{ height: calc\(100vh - var\(--topbar-h\)\); padding-bottom: 12px; overflow: hidden; overflow: clip; \}/.test(flat));
});

test("long emails, file names and digests wrap rather than run out of their box (#571)", () => {
  assert.ok(/\nbody \{[^}]*overflow-wrap: break-word/.test(css));
  // In flex and grid an item is as wide as its longest word unless told otherwise
  assert.ok(/\.page-head > \* \{[^}]*min-width: 0/.test(css));
  assert.ok(/\.pod-facts dd \{[^}]*min-width: 0/.test(css));
  assert.ok(/\.pod-about code \{[^}]*overflow-wrap: anywhere/.test(css));
  assert.ok(/\.chip \{[^}]*overflow-wrap: anywhere/.test(css));
  // Not for every table cell: a wide table scrolls in its own box instead of breaking every word
  assert.ok(!/overflow-wrap: anywhere/.test(css.match(/\nth, td \{[^}]*\}/)?.[0] ?? "anywhere"));
});
