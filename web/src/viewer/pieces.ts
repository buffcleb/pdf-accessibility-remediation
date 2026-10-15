import type { TagElement } from "../api";

/** The pieces of a tag's own text that can be split inside: its pieces of text, not its pictures or drawings (a tag
 * the tag map gives no pieces for is one piece, its text, when it has no tags inside). */
export function splitPieces(element: TagElement): { k: number; text: string }[] {
  return element.parts
    ? element.parts.filter((p) => p.kind === "content" && p.text && !/^\((image|drawing)\)$/.test(p.text))
      .map((p) => ({ k: p.k, text: p.text ?? "" }))
    : element.own && element.kids.length === 0 && element.text ? [{ k: 0, text: element.text }] : [];
}
