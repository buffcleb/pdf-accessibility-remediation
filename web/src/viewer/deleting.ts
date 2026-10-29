/** Removing or deleting tags: what each choice would do to a tag, and whether it's allowed, with the unsaved changes
 * applied. Shared by the one-tag question and the one for several tags chosen together. */
import type { TagEdit, TagMap } from "../api";
import { addEdit, draftTree, keptHolders, overrides } from "./draft";
import { CONTENT_ROLES, checkMove } from "./roles";

export type DeleteChoice = "lift" | "delete";

export type DeleteCheck = {
  role: string;
  parentRole: string | null;
  kids: number[];       // the tags directly inside it
  going: number[];      // the tags inside it that deleting it everything takes too
  kept: number[];       // links and form fields inside it, which stay tagged and move up
  keepsOwn: boolean;    // its own text stays, read as part of its parent
  single: boolean;      // nothing inside and a parent that can't take its text: the two choices are the same
  liftWhy: string;      // why "remove only this tag" can't be done, or ""
  deleteWhy: string;    // why "delete it and everything in it" can't be done, or ""
};

export function deleteCheck(map: TagMap, ops: TagEdit[], i: number): DeleteCheck {
  const element = map.elements[i];
  const tree = draftTree(map, ops);
  const over = overrides(ops, map);
  const roleOf = (k: number | null | undefined) => (k == null ? null : over.get(k)?.role ?? map.elements[k]?.role ?? null);
  const role = roleOf(i)!;
  const parentRole = roleOf(tree.parent.get(i));
  const kids = tree.kids.get(i) ?? [];
  const inside: number[] = [];
  for (const stack = [...kids]; stack.length;) { const k = stack.pop()!; inside.push(k); stack.push(...(tree.kids.get(k) ?? [])); }
  // Links and form fields inside it have to stay tagged: deleting it keeps them (whole) and moves them up
  const kept = keptHolders(map, tree.kids, i);
  const keptInside = new Set<number>();
  for (const stack = [...kept]; stack.length;) { const k = stack.pop()!; keptInside.add(k); stack.push(...(tree.kids.get(k) ?? [])); }
  const going = inside.filter((k) => !keptInside.has(k));
  const keptBlocked = kept.map((k) => checkMove(roleOf(k)!, roleOf(tree.parent.get(k)), parentRole, false)).find((v) => !v.ok);
  const keepsOwn = !!parentRole && CONTENT_ROLES.has(parentRole);
  const blocked = kids.map((k) => checkMove(roleOf(k)!, role, parentRole, false)).find((v) => !v.ok);
  const top = tree.parent.get(i) === null ? "The document's top tag stays." : "";
  return {
    role, parentRole, kids, going, kept, keepsOwn, single: kids.length === 0 && !keepsOwn,
    liftWhy: top || (element.annot ? "It holds a form field or link, which has to stay tagged."
      : blocked ? `The tags inside can't move up a level. ${blocked.reason}` : ""),
    deleteWhy: top || (element.annot ? "This tag is a link or form field itself, which has to stay tagged."
      : keptBlocked ? `It holds a link or form field, which has to stay tagged and can't move up a level. ${keptBlocked.reason} Move it out first.` : ""),
  };
}

/** Several tags removed or deleted together, in the order given (reading order): each is checked with the ones before
 * it already applied. A tag inside one deleted with everything in it goes with it and needs nothing of its own. */
export function deleteMany(map: TagMap, ops: TagEdit[], ids: number[], children: DeleteChoice):
  { ops: TagEdit[]; done: number[]; within: number[]; refused: { i: number; why: string }[] } {
  let next = ops;
  const done: number[] = [], within: number[] = [], refused: { i: number; why: string }[] = [];
  for (const i of ids) {
    const el = map.elements[i];
    if (!el) continue;
    if (draftTree(map, next).deleted.has(i)) { within.push(i); continue; }
    const c = deleteCheck(map, next, i);
    // With nothing inside, removing the tag is the same as deleting it
    const how: DeleteChoice = c.single ? "delete" : children;
    const why = how === "lift" ? c.liftWhy : c.deleteWhy;
    if (why) { refused.push({ i, why }); continue; }
    next = addEdit(next, { op: "delete", element: i, expect_role: el.role, children: how });
    done.push(i);
  }
  return { ops: next, done, within, refused };
}
