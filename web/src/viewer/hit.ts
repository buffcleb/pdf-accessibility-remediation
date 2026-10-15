/** Which tag a click on the page means. Tags sit inside each other (a list, its item, the item's body), and the
 * outline of the outer one covers the inner ones, so a click is taken to mean the innermost tag under the pointer.
 * Clicking the same place again goes one tag out, and so on round. */
import type { TagMap } from "../api";

/** The tags with a box on `page` holding the point (in page units), innermost first. The document's top tag and
 * tags in `skip` (merged away or deleted by unsaved edits) aren't counted. */
export function tagsAt(map: TagMap, page: number, x: number, y: number, skip: Set<number> = new Set()): number[] {
  const depth = (i: number) => { let d = 0; for (let p = map.elements[i]?.parent; p !== null && p !== undefined; p = map.elements[p]?.parent) d++; return d; };
  const found: { i: number; depth: number; area: number }[] = [];
  for (const e of map.elements) {
    if (e.parent === null || skip.has(e.i)) continue;
    const box = e.boxes.find((b) => b.page === page && x >= b.bbox[0] - 1 && x <= b.bbox[2] + 1 && y >= b.bbox[1] - 1 && y <= b.bbox[3] + 1);
    if (box) found.push({ i: e.i, depth: depth(e.i), area: (box.bbox[2] - box.bbox[0]) * (box.bbox[3] - box.bbox[1]) });
  }
  return found.sort((a, b) => b.depth - a.depth || a.area - b.area).map((f) => f.i);
}

/** What last happened at a place: the innermost tag there, and the tag the click chose. */
export type LastHit = { first: number; chose: number } | null;

/** The tag a click chooses: the innermost under the pointer; or, when the click repeats the last one (same place,
 * and its choice is still the selection), the next tag out, back to the innermost after the outermost. */
export function chooseAt(here: number[], selected: number | null, last: LastHit): number | null {
  if (!here.length) return null;
  const at = selected === null ? -1 : here.indexOf(selected);
  if (last && last.first === here[0] && last.chose === selected && at >= 0) return here[(at + 1) % here.length];
  return here[0];
}
