// The unsaved changes' history (#365): undo and redo in order, a new change drops what was undone, and the keys.
import { test } from "node:test";
import assert from "node:assert/strict";
import { MAX_STEPS, fresh, historyKey, push, redo, undo } from "../src/viewer/history";

test("changes are undone and redone in order, and a new change ends the redo", () => {
  let h = fresh<string[]>([]);
  const a = ["a"], ab = ["a", "b"], abc = ["a", "b", "c"];
  h = push(push(push(h, a), ab), abc);
  assert.deepEqual(h.present, abc);
  h = undo(undo(h));
  assert.deepEqual([h.present, h.past.length, h.future.length], [a, 1, 2]);
  h = redo(h);
  assert.equal(h.present, ab);
  const ax = ["a", "x"];
  h = push(undo(h), ax);  // after undoing, a different change: what was undone can't come back
  assert.deepEqual([h.present, h.future.length], [ax, 0]);
  assert.equal(redo(h), h);            // nothing to redo
  assert.equal(undo(undo(undo(h))).present.length, 0);
  assert.equal(push(h, h.present), h); // the same list again isn't a step
});

test("the history keeps the last steps only", () => {
  let h = fresh(0);
  for (let n = 1; n <= MAX_STEPS + 50; n++) h = push(h, n);
  assert.equal(h.past.length, MAX_STEPS);
  assert.equal(h.past[0], 50);
});

test("Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y (or Cmd), but not while typing in a field", () => {
  const k = (key: string, mods: Partial<Record<"ctrlKey" | "metaKey" | "shiftKey" | "altKey", boolean>> = {}) =>
    ({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });
  const page = { tagName: "DIV", isContentEditable: false } as unknown as EventTarget;
  const field = { tagName: "TEXTAREA", isContentEditable: false } as unknown as EventTarget;
  assert.equal(historyKey(k("z", { ctrlKey: true }), page), "undo");
  assert.equal(historyKey(k("z", { metaKey: true }), page), "undo");
  assert.equal(historyKey(k("Z", { metaKey: true, shiftKey: true }), page), "redo");
  assert.equal(historyKey(k("y", { ctrlKey: true }), page), "redo");
  assert.equal(historyKey(k("z"), page), null);
  assert.equal(historyKey(k("z", { ctrlKey: true, altKey: true }), page), null);
  assert.equal(historyKey(k("z", { ctrlKey: true }), field), null);  // the field undoes its own typing
});
