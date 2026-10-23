/** A tag's words and details can always be read in full (#436): the tree row's short form and its title, and where
 * the hover and focus card goes on the page so none of it is cut off by the pane's edges. */
import type { TagElement } from "../api";

const SNIPPET = 70;

/** A tag's words as fully as the tag map has them: `text` is cut short (at 200 characters), `say` is the tag's own
 * text in full when that differs. `say` is the longer one unless child tags hold some of the words. */
export function tagText(el: TagElement): string | undefined {
  return el.say && el.say.length > (el.text?.length ?? 0) ? el.say : el.text;
}

/** What a tree row says of a tag: its alt text, read-aloud text, words or field name, in full and cut short. */
export function rowWords(el: TagElement, alt?: string): { full: string; short: string; cut: boolean } {
  const a = alt ?? el.alt;
  const full = a ? `alt: ${a}` : el.actual_text ?? tagText(el) ?? (el.name ? `field: ${el.name}` : "");
  const cut = full.length > SNIPPET;
  return { full, short: cut ? `${full.slice(0, SNIPPET)}…` : full, cut };
}

/** A tree row's title (its tooltip), a line each: what it is, its words in full when the row cuts them, the rest. */
export function rowTitle(name: string, words: { full: string; cut: boolean }, rest: string[]): string {
  return [name, ...(words.cut ? [words.full] : []), ...rest].join("\n");
}

export type Edges = { left: number; top: number; right: number; bottom: number };
export type CardPlace = { left: number; top: number; width: number; maxHeight: number;
  side: "above" | "below" | "off" };

export const CARD_W = 330;     // the card's width when there's room for it
export const CARD_MAX_H = 360; // taller than this, it scrolls
const GAP = 6;                 // between the tag's outline and the card
const MARGIN = 8;              // between the card and the pane's edges

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, hi));

/** The card's width in a view: its own, or the view's less a margin each side when that's narrower. */
export function cardWidth(view: Edges, w = CARD_W): number {
  return Math.max(0, Math.min(w, view.right - view.left - 2 * MARGIN));
}

/** Where the card for a tag goes, all in one frame (the page's pixels): above the tag's outline (`anchor`) when it
 * fits there, below it when it doesn't, and always inside the part of the pane that can be seen (`view`): moved in
 * from an edge rather than cut by it. `size` is the card's own width and height; it's no wider or taller than the
 * view, and no taller than CARD_MAX_H (it scrolls past that). "off": the tag is scrolled out of sight, and so is
 * the card. */
export function placeCard(anchor: Edges, size: { w: number; h: number }, view: Edges): CardPlace {
  const width = cardWidth(view, size.w);
  const maxHeight = Math.max(0, Math.min(CARD_MAX_H, view.bottom - view.top - 2 * MARGIN));
  const h = Math.min(size.h, maxHeight);
  const above = anchor.top - GAP - (view.top + MARGIN);
  const below = view.bottom - MARGIN - (anchor.bottom + GAP);
  const gone = anchor.bottom < view.top || anchor.top > view.bottom || anchor.right < view.left
    || anchor.left > view.right;
  const side = gone ? "off" : above >= h ? "above" : below >= h ? "below" : above >= below ? "above" : "below";
  const want = side === "above" ? anchor.top - GAP - h : anchor.bottom + GAP;
  const top = clamp(want, view.top + MARGIN, view.bottom - MARGIN - h);
  const left = clamp(anchor.left, view.left + MARGIN, view.right - MARGIN - width);
  return { left, top, width, maxHeight, side };
}
