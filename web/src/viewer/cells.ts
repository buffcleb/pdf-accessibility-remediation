// A table cell's headers (the header cells it's read with), for the edit form: which can be chosen, and which are.
import type { TagEdit, TagElement, TagMap } from "../api";
import { overrides } from "./draft";

/** The table a cell is in, or null. */
export function tableOf(map: TagMap, i: number): number | null {
  for (let p = map.elements[i]?.parent ?? null, hops = 0; p !== null && hops < 4; p = map.elements[p]?.parent ?? null, hops++) {
    if (map.elements[p]?.role === "Table") return p;
  }
  return null;
}

/** The header cells of the cell's table, in table order (row by row), with pending type changes applied: the ones
 * the engine accepts as its headers. The cell itself and the cells of a table nested in one of its cells are left
 * out. */
export function headerChoices(map: TagMap, ops: TagEdit[], i: number): TagElement[] {
  const table = tableOf(map, i);
  if (table === null) return [];
  const over = overrides(ops, map);
  return map.elements.filter((e) => e.i !== i && (over.get(e.i)?.role ?? e.role) === "TH" && tableOf(map, e.i) === table);
}

/** The headers a cell names, with a pending change to them applied. */
export function headersOf(map: TagMap, ops: TagEdit[], i: number): number[] {
  let out = map.elements[i]?.headers ?? [];
  for (const op of ops) if (op.op === "set_headers" && op.element === i) out = op.headers;
  return out;
}
