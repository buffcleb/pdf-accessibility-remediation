// A tag pending edits make, chosen before it's saved (#427): its type and alt text changed in the edit that makes it.
// Taking it back, and everything else done to it, is in made.ts (#692).
import type { TagEdit } from "../api";
import { NEW_AREA_ROLES } from "./roles";

/** Types the new part of a split text tag can have (the engine's SPLIT_ROLES). */
export const SPLIT_ROLES = NEW_AREA_ROLES.filter((r) => r !== "Figure" && r !== "Formula");

/** Whether the edit at this place makes a tag the New tag bar changes: its type and alt text are the edit's own (a
 * tag from an area, the new part of a split, a figure's part). A built list's or table's are changed as tags. */
export function makesTag(op: TagEdit | undefined): op is Extract<TagEdit, { op: "tag_artifacts" | "split" | "regroup" | "take_pieces" | "cut_area" }> {
  return op?.op === "tag_artifacts" || op?.op === "split" || op?.op === "regroup" || op?.op === "take_pieces" || op?.op === "cut_area";
}

/** The types the tag an edit makes can be given (a part of a picture is a figure or formula). */
export function pendingRoles(op: TagEdit, group = 0): string[] {
  if (op.op === "regroup") return op.groups[group]?.box ? ["Figure", "Formula"] : NEW_AREA_ROLES;
  return op.op === "tag_artifacts" || op.op === "take_pieces" || op.op === "cut_area" ? NEW_AREA_ROLES
    : op.op === "split" ? SPLIT_ROLES : [];
}

export const needsAlt = (role: string) => role === "Figure" || role === "Formula";

/** The edits with the tag the edit at `at` makes given a new type (and alt text, which a figure or formula needs).
 * Null when that can't be: the edit makes no tag, the type isn't one it can have, or a figure has no alt text. A split
 * part given its tag's own type drops the role, as the engine would then do nothing with it. */
export function changePending(ops: TagEdit[], at: number, role: string, alt: string,
  ownRole?: string, group = 0): TagEdit[] | null {
  const op = ops[at];
  if (!makesTag(op) || !pendingRoles(op, group).includes(role)) return null;
  let next: TagEdit;
  if (op.op === "regroup") {
    if (needsAlt(role) && !alt.trim()) return null;
    next = { ...op, groups: op.groups.map((g, n) => {
      if (n !== group) return g;
      const { alt: _old, ...rest } = g;
      return { ...rest, role, ...(needsAlt(role) ? { alt: alt.trim() } : {}) };
    }) };
  } else if (op.op === "tag_artifacts" || op.op === "take_pieces" || op.op === "cut_area") {
    if (needsAlt(role) && !alt.trim()) return null;
    const { alt: _old, ...rest } = op;
    next = { ...rest, role, ...(needsAlt(role) ? { alt: alt.trim() } : {}) };
  } else {
    const { role: _old, ...rest } = op;
    next = role === (ownRole ?? op.expect_role) ? rest : { ...rest, role };
  }
  return ops.map((o, n) => (n === at ? next : o));
}

/** The place of the edit that makes a tag, when `next` is `prev` with one such edit added at its end. */
export function justMade(prev: TagEdit[], next: TagEdit[]): number | null {
  return next.length === prev.length + 1 && prev.every((o, n) => o === next[n]) && makesTag(next[next.length - 1])
    ? next.length - 1 : null;
}
