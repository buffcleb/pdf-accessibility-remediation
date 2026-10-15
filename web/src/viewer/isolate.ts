/** Isolating a tag: everything outside it (and the tags inside it) is veiled in the tree and on the page, so
 * it's clear where to look. */
import type { TagMap } from "../api";
import type { MenuItem } from "./menu";

/** The tag and every tag inside it. */
export function subtree(map: TagMap, i: number): Set<number> {
  const out = new Set<number>();
  const stack = [i];
  while (stack.length) {
    const k = stack.pop()!;
    if (out.has(k) || !map.elements[k]) continue;
    out.add(k);
    stack.push(...map.elements[k].kids);
  }
  return out;
}

/** The parts of a page the isolated tag (with what's inside it) covers: the holes left in the veil. */
export function holes(map: TagMap, inside: Set<number>, page: number): [number, number, number, number][] {
  const out: [number, number, number, number][] = [];
  for (const i of inside) for (const b of map.elements[i]?.boxes ?? []) if (b.page === page) out.push(b.bbox);
  return out;
}

/** The menu's line for it: isolate the tag, or, while one is isolated, show everything again. */
export function isolateItem(isolated: number | null, id: number | null): MenuItem {
  return isolated !== null
    ? { id: "unisolate", group: "more", label: "Show everything again", note: "Ends the isolation.", act: "unisolate" }
    : { id: "isolate", group: "more", label: "Isolate this tag", note: "Veils the rest of the tree and the page.", act: "isolate", disabled: id === null ? "Select a tag first." : undefined };
}
