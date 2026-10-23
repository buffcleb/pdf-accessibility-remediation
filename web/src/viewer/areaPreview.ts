/** Pending edits that make or reshape tags, previewed as they will be saved (#427): a new tag from an area of
 * untagged content (tag_artifacts) or from the end of a tag (split), and content moved between tags (assign). Kept
 * apart from the pages so it can be tested without a browser. */
import type { TagEdit, TagElement, TagMap } from "../api";
import { unite, type Box } from "./outline";

/** A tag a pending edit will make, read right before or after the tag it's placed by. */
/** op: the place in the pending edits of the edit making it, which stands for it until it's saved (#427). */
export type NewTag = { anchor: number; position: "before" | "after"; role: string; alt: string; text: string; boxes: Box[];
  op: number };

const join = (texts: (string | undefined)[]) => texts.map((t) => (t ?? "").trim()).filter(Boolean).join(" ");

/** The tags pending edits will make, in the order of the edits. A split's new tag has its tag's type and the /K
 * items from `at` on; the tag keeps the rest (see `splitKeeps`). */
export function newTags(map: TagMap, ops: TagEdit[]): NewTag[] {
  const arts = new Map((map.artifacts ?? []).map((a) => [`${a.page}:${a.seq}`, a]));
  const out: NewTag[] = [];
  ops.forEach((op, at) => {
    if (op.op === "tag_artifacts") {
      const got = op.sections.map((s) => arts.get(`${op.page}:${s}`)).filter((a) => a !== undefined);
      out.push({ anchor: op.element, position: op.position, role: op.role, alt: op.alt ?? "", text: join(got.map((a) => a.text)),
        boxes: unite(got.map((a) => ({ page: a.page, bbox: a.bbox }))), op: at });
    } else if (op.op === "split") {
      // From /K item `at` on; split inside that item's text (`char`), the new tag starts at that character
      const e = map.elements[op.element];
      const moved = (e?.parts ?? []).filter((p) => p.k >= op.at);
      const texts = moved.map((p) => (p.k === op.at && op.char ? (p.text ?? "").slice(op.char) : p.text));
      // A tag whose text the tag map keeps on the tag, not its pieces (one piece, or no parts listed: as the split
      // picker reads it): cut the tag's own text there, and draw it in the tag's box
      const parts = e?.parts ?? [];
      const whole = parts.length <= 1 && !parts[0]?.text ? (e?.say ?? e?.text ?? "") : "";
      const text = join(texts) || (whole && op.at === 0 ? whole.slice(op.char ?? 0).trim() : "");
      const boxes = unite(moved.filter((p) => p.page && p.bbox).map((p) => ({ page: p.page!, bbox: p.bbox! })));
      out.push({ anchor: op.element, position: "after", role: op.role ?? e?.role ?? "P", alt: "", text,
        boxes: boxes.length || parts.length ? boxes : (e?.boxes ?? []).map((b) => ({ page: b.page, bbox: b.bbox })),
        op: at });
    }
  });
  return out;
}

/** What a split tag keeps: its boxes from the /K items before the cut (null: it isn't split, or its parts don't say). */
export function splitKeeps(map: TagMap, ops: TagEdit[]): Map<number, Box[]> {
  const out = new Map<number, Box[]>();
  for (const op of ops) {
    if (op.op !== "split") continue;
    const parts = map.elements[op.element]?.parts ?? [];
    // A split inside an item's text leaves that item in both
    const kept = parts.filter((p) => (p.k < op.at || (p.k === op.at && !!op.char)) && p.page && p.bbox)
      .map((p) => ({ page: p.page!, bbox: p.bbox! }));
    if (parts.some((p) => p.page && p.bbox)) out.set(op.element, unite(kept));
  }
  return out;
}

/** What a split tag reads once saved: its text before the cut (only where the tag map gives the text to cut). */
export function splitKeptText(map: TagMap, ops: TagEdit[]): Map<number, string> {
  const out = new Map<number, string>();
  for (const op of ops) {
    if (op.op !== "split") continue;
    const e = map.elements[op.element];
    const parts = e?.parts ?? [];
    const texts = parts.filter((p) => p.k < op.at || (p.k === op.at && !!op.char))
      .map((p) => (p.k === op.at && op.char ? (p.text ?? "").slice(0, op.char) : p.text));
    const whole = parts.length <= 1 && !parts[0]?.text ? (e?.say ?? e?.text ?? "") : "";
    const text = join(texts) || (whole && op.at === 0 && op.char ? whole.slice(0, op.char).trim() : "");
    if (text) out.set(op.element, text);
  }
  return out;
}

/** Content moved between tags by pending area edits (assign): the boxes of each tag that gives content up, once
 * saved; the boxes each tag gains (drawn beside its own: a heading taking in a line further down isn't one box over
 * everything between); and the tags left with nothing (the engine removes them). A tag is only redrawn from its pieces
 * when they are all its content: one also holding content outside the page's own stream keeps its boxes. */
export function contentMoves(map: TagMap, ops: TagEdit[]): { boxes: Map<number, Box[]>; added: Map<number, Box[]>; emptied: Set<number> } {
  const owner = new Map((map.pieces ?? []).map((p) => [`${p.page}:${p.mcid}`, p.el]));
  const piece = new Map((map.pieces ?? []).map((p) => [`${p.page}:${p.mcid}`, p]));
  const touched = new Set<number>();  // tags giving content up
  const added = new Map<number, Box[]>();
  for (const op of ops) {
    if (op.op !== "assign") continue;
    for (const p of op.pieces) {
      const k = `${p.page}:${p.mcid}`;
      const from = owner.get(k);
      if (from === undefined || from === op.element) continue;
      touched.add(from);
      owner.set(k, op.element);
      const pc = piece.get(k)!;
      added.set(op.element, [...(added.get(op.element) ?? []), { page: pc.page, bbox: pc.bbox }]);
    }
  }
  const held = new Map<number, Box[]>();
  for (const [k, el] of owner) {
    const pc = piece.get(k)!;
    held.set(el, [...(held.get(el) ?? []), { page: pc.page, bbox: pc.bbox }]);
  }
  const ownPieces = new Map<number, number>();
  for (const p of map.pieces ?? []) ownPieces.set(p.el, (ownPieces.get(p.el) ?? 0) + 1);
  const allInPieces = (e: TagElement | undefined) =>
    !!e && (e.parts ?? []).filter((p) => p.kind === "content").length <= (ownPieces.get(e.i) ?? 0);
  const boxes = new Map<number, Box[]>();
  const emptied = new Set<number>();
  for (const i of touched) {
    if (added.has(i) && held.get(i)?.length) continue;  // took content back in as well: drawn with what it gains
    const e = map.elements[i];
    const now = held.get(i) ?? [];
    if (!allInPieces(e)) continue;  // it holds more than its pieces: drawn as it is
    if (!now.length && !(e?.kids ?? []).length) emptied.add(i);
    else boxes.set(i, unite(now));
  }
  return { boxes, added: new Map([...added].map(([i, b]) => [i, unite(b)])), emptied };
}
