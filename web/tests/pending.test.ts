// A tag pending edits make can be chosen before saving: its type and alt text changed, or it taken back (#427).
// Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import type { TagEdit } from "../src/api";
import { SPLIT_ROLES, changePending, justMade, makesTag, pendingRoles } from "../src/viewer/pending";

const AREA: TagEdit = { op: "tag_artifacts", element: 3, expect_role: "P", position: "after", page: 1, sections: [0, 1],
  role: "P" };
const SPLIT: TagEdit = { op: "split", element: 5, expect_role: "P", at: 2 };
const MOVE: TagEdit = { op: "move", element: 1, expect_role: "P", target: 2, position: "before" } as TagEdit;

test("tags made from an area or a split can be chosen; other edits make none", () => {
  assert.ok(makesTag(AREA) && makesTag(SPLIT) && !makesTag(MOVE) && !makesTag(undefined));
  assert.ok(pendingRoles(AREA).includes("Figure"));
  assert.ok(!pendingRoles(SPLIT).includes("Figure") && SPLIT_ROLES.includes("H2"));
});

test("a new tag's type and alt text are changed in the edit that makes it", () => {
  const ops = [MOVE, AREA];
  assert.deepEqual(changePending(ops, 1, "H2", "")?.[1], { ...AREA, role: "H2" });
  assert.equal(changePending(ops, 1, "Figure", "  "), null); // a figure needs its alt text
  assert.deepEqual(changePending(ops, 1, "Figure", " A map ")?.[1], { ...AREA, role: "Figure", alt: "A map" });
  // Back to text: the alt text goes with the figure
  const fig = changePending(ops, 1, "Figure", "A map")!;
  assert.deepEqual(changePending(fig, 1, "P", "A map")?.[1], AREA);
  assert.equal(changePending(ops, 0, "P", ""), null); // a move makes no tag
  assert.equal(changePending(ops, 1, "TD", ""), null); // not a type a new area can have
  assert.equal(ops[1], AREA); // the edits given are left as they were
});

test("a split's new part gets a type of its own, or drops it when it's its tag's type", () => {
  const typed = changePending([SPLIT], 0, "H3", "")!;
  assert.deepEqual(typed[0], { ...SPLIT, role: "H3" });
  assert.deepEqual(changePending(typed, 0, "P", "")?.[0], SPLIT);
  assert.equal(changePending([SPLIT], 0, "Figure", "x"), null);
});

test("a new tag just made is found", () => {
  assert.equal(justMade([MOVE], [MOVE, AREA]), 1);
  assert.equal(justMade([MOVE], [MOVE, MOVE]), null);
  assert.equal(justMade([MOVE, AREA], [MOVE]), null);
  assert.equal(justMade([MOVE], [{ ...MOVE }, AREA]), null); // an earlier edit changed too: not just a new one
});

test("an area's new tags are chosen one by one: each changed in the one edit making them", () => {
  const REGROUP: TagEdit = { op: "regroup", element: 3, expect_role: "P", position: "before", page: 1, groups: [
    { role: "P", pieces: [1] }, { role: "Figure", box: [0, 0, 50, 50], alt: "A board" }] };
  assert.ok(makesTag(REGROUP));
  assert.deepEqual(pendingRoles(REGROUP, 1), ["Figure", "Formula"]); // a part of a picture stays a figure or formula
  const ops = [MOVE, REGROUP];
  const h = changePending(ops, 1, "H2", "", undefined, 0)!;
  assert.deepEqual((h[1] as typeof REGROUP).groups[0], { role: "H2", pieces: [1] });
  assert.equal(changePending(ops, 1, "P", "", undefined, 1), null);
  assert.equal(changePending(ops, 1, "Figure", " ", undefined, 1), null);
});
