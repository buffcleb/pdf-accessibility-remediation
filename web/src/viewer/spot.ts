// Show for a review item (#426): where it opens the tag manager, and the area on the page it's about
import type { Finding, TagMap } from "../api";

export type Box = [number, number, number, number];
/** The area a review item is about, highlighted on its page. No boxes: the item is about the whole page. */
export type Spot = { page: number; boxes: Box[]; issue: string; key?: string };

type Item = Finding & { element?: number };

/** The tag manager at the item: on its version, at its page, with its tag selected when it has one. Null when the
 * item has neither a page nor a tag to go to. */
export function showUrl(jobId: string, f: Item, version = 1): string | null {
  if (f.page == null && f.element == null) return null;
  const q = new URLSearchParams({ v: String(version) });
  if (f.page != null) q.set("page", String(f.page));
  if (f.element != null) q.set("el", String(f.element));
  if (f.key) q.set("f", f.key);
  return `/jobs/${jobId}/tags?${q}`;
}

/** What to highlight for an item: the boxes the checks gave it, or else its tag's boxes on its page (on the tag's
 * first page when the item names none). Null when there's no page to show. */
export function spotFor(f: Item, map: TagMap | null): Spot | null {
  const el = f.element != null ? map?.elements[f.element] : undefined;
  const page = f.page ?? el?.boxes[0]?.page;
  if (page == null || (map && !map.pages[page - 1])) return null;
  const boxes: Box[] = f.boxes?.length ? f.boxes.map((b) => [b[0], b[1], b[2], b[3]])
    : (el?.boxes ?? []).filter((b) => b.page === page).map((b) => b.bbox);
  return { page, boxes, issue: f.issue, ...(f.key ? { key: f.key } : {}) };
}

/** The item a Show link names: by its key, else the first item on that page about that tag. */
export function findItem(findings: Item[], key: string | null, page: number | null,
  element: number | null): Item | null {
  if (key) {
    const f = findings.find((x) => x.key === key);
    if (f) return f;
  }
  if (page == null) return null;
  return findings.find((x) => x.page === page && (element == null || x.element === element)) ?? null;
}

/** The area around a spot's boxes, to scroll it into view. Null for a whole-page spot. */
export function spotExtent(s: Spot): Box | null {
  if (!s.boxes.length) return null;
  return [Math.min(...s.boxes.map((b) => b[0])), Math.min(...s.boxes.map((b) => b[1])),
    Math.max(...s.boxes.map((b) => b[2])), Math.max(...s.boxes.map((b) => b[3]))];
}

/** What a screen reader hears when a spot is shown. */
export function spotWords(s: Spot): string {
  return `Showing: ${s.issue}, page ${s.page}.`;
}
