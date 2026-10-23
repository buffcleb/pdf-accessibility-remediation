// A tag pending edits make, chosen before it's saved (#427): its type and alt text changed, or the edit taken back.
// It has no number until the engine saves it, so the edit that makes it stands for it: changing it changes that edit.
import type { TagEdit } from "../api";
import { NEW_AREA_ROLES } from "./roles";

/** Types the new part of a split text tag can have (the engine's SPLIT_ROLES). */
export const SPLIT_ROLES = NEW_AREA_ROLES.filter((r) => r !== "Figure" && r !== "Formula");

/** Whether the edit at this place makes a tag that can be chosen before saving. */
export function makesTag(op: TagEdit | undefined): op is Extract<TagEdit, { op: "tag_artifacts" | "split" }> {
  return op?.op === "tag_artifacts" || op?.op === "split";
}

/** The types the tag an edit makes can be given. */
export function pendingRoles(op: TagEdit): string[] {
  return op.op === "tag_artifacts" ? NEW_AREA_ROLES : op.op === "split" ? SPLIT_ROLES : [];
}

export const needsAlt = (role: string) => role === "Figure" || role === "Formula";

/** The edits with the tag the edit at `at` makes given a new type (and alt text, which a figure or formula needs).
 * Null when that can't be: the edit makes no tag, the type isn't one it can have, or a figure has no alt text. A split
 * part given its tag's own type drops the role, as the engine would then do nothing with it. */
export function changePending(ops: TagEdit[], at: number, role: string, alt: string,
  ownRole?: string): TagEdit[] | null {
  const op = ops[at];
  if (!makesTag(op) || !pendingRoles(op).includes(role)) return null;
  let next: TagEdit;
  if (op.op === "tag_artifacts") {
    if (needsAlt(role) && !alt.trim()) return null;
    const { alt: _old, ...rest } = op;
    next = { ...rest, role, ...(needsAlt(role) ? { alt: alt.trim() } : {}) };
  } else {
    const { role: _old, ...rest } = op;
    next = role === (ownRole ?? op.expect_role) ? rest : { ...rest, role };
  }
  return ops.map((o, n) => (n === at ? next : o));
}

/** The edits without the one at `at` (the tag it would make isn't made). */
export function dropPending(ops: TagEdit[], at: number): TagEdit[] {
  return ops.filter((_, n) => n !== at);
}

/** The place of the edit that makes a tag, when `next` is `prev` with one such edit added at its end. */
export function justMade(prev: TagEdit[], next: TagEdit[]): number | null {
  return next.length === prev.length + 1 && prev.every((o, n) => o === next[n]) && makesTag(next[next.length - 1])
    ? next.length - 1 : null;
}
