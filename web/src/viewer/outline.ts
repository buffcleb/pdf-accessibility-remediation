/** Building a list from the lines of a paragraph (or of several tags): the lines as rows, a first guess at which
 * start an item and how deep each sits, the changes a person makes to that, and the edit it all becomes. Kept apart
 * from the dialog so it can be tested. */
import type { ListRow, TagEdit, TagElement, TagMap } from "../api";

/** What a row can be: an item of the list, or a tag of its own that stays outside it. */
export const ROW_KINDS = ["item", "P", "H1", "H2", "H3", "H4", "H5", "H6"] as const;
export type RowKind = (typeof ROW_KINDS)[number];

export type OutlineLine = {
  el: number;        // the tag the line is in
  k: number;         // the item of that tag's content the line starts at
  text: string;
  x: number;         // where the line starts on the page
  start: boolean;    // it starts a row (false: it carries on the row above, as a wrapped line does)
  kind: RowKind;
  indent: number;    // how deep a list item sits, from 0; made to fit by `settle`
};

// "1.", "2.3.1.", "(a)", "iv)", a bullet or a dash at the start of a line
const LABEL = /^\s*(?:\(?\d+(?:\.\d+)*[.)]?|\(?[A-Za-z][.)]|\(?[ivxlc]+[.)]|[\u2022\u25E6\u25AA\u25A0\u25CF\u25CB\u00B7\u2023\u27A2\u2756*\-\u2013\u2014])\s/i;

/** The tags' content as lines of text, in order, with a first guess: a line that starts with a number or a bullet
 * starts a row and the others carry on the row above (when no line has one, every line is a row), and rows that
 * start further right sit deeper. */
export function outlineLines(els: TagElement[]): OutlineLine[] {
  const out: OutlineLine[] = [];
  for (const e of els) {
    const mine: (OutlineLine & { y0: number; y1: number; right: number; page: number })[] = [];
    for (const p of e.parts ?? []) {
      const last = mine[mine.length - 1];
      if (!p.bbox || p.page === undefined) {  // a link or a piece with no place: it goes with the line it's in
        if (last) last.text += p.text ?? "";
        else if (p.text) mine.push({ el: e.i, k: p.k, text: p.text, x: 0, start: true, kind: "item", indent: 0, y0: -1, y1: -1, right: 0, page: -1 });
        continue;
      }
      const [x0, y0, x1, y1] = p.bbox;
      if (last && last.page === p.page && Math.abs(y0 - last.y0) <= Math.max(2, (last.y1 - last.y0) / 2)) {
        last.text += (x0 - last.right > 1.5 && !/\s$/.test(last.text) ? " " : "") + (p.text ?? "");
        last.right = Math.max(last.right, x1);
        last.x = Math.min(last.x, x0);
      } else {
        mine.push({ el: e.i, k: p.k, text: p.text ?? "", x: x0, start: true, kind: "item", indent: 0, y0, y1, right: x1, page: p.page });
      }
    }
    if (!mine.length) mine.push({ el: e.i, k: 0, text: e.text ?? "", x: e.boxes[0]?.bbox[0] ?? 0, start: true, kind: "item", indent: 0, y0: 0, y1: 0, right: 0, page: 0 });
    const labeled = mine.map((l) => LABEL.test(l.text));
    mine.forEach((l, i) => { l.start = i === 0 || !labeled.includes(true) || labeled[i]; });
    for (const { y0: _a, y1: _b, right: _c, page: _d, ...line } of mine) out.push(line);
  }
  // Depth from where the rows start: each distinct left edge (within a few points) is a level
  const edges: number[] = [];
  for (const x of out.filter((l) => l.start).map((l) => l.x).sort((a, b) => a - b)) {
    if (!edges.length || x - edges[edges.length - 1] > 6) edges.push(x);
  }
  for (const l of out) l.indent = Math.max(0, edges.findIndex((edge, i) => l.x <= edge + 6 || i === edges.length - 1));
  return settle(out);
}

/** The lines with every list item's depth made to fit: the first item of a list is at the top level, and an item
 * is at most one level deeper than the item before it. A row that isn't an item ends the list before it. */
export function settle(lines: OutlineLine[]): OutlineLine[] {
  const out = lines.map((l) => ({ ...l }));
  if (out.length) out[0].start = true;
  let run: OutlineLine[] = [];
  const close = () => {
    const least = Math.min(...run.map((l) => l.indent));
    let before = -1;
    for (const l of run) before = l.indent = Math.max(0, Math.min(before + 1, l.indent - least));
    run = [];
  };
  for (const l of out) {
    if (!l.start) continue;
    if (l.kind === "item") run.push(l);
    else { if (run.length) close(); l.indent = 0; }
  }
  if (run.length) close();
  return out;
}

/** The row before row `i` that is a list item in the same list, or -1. */
function itemBefore(lines: OutlineLine[], i: number): number {
  for (let j = i - 1; j >= 0; j--) {
    if (!lines[j].start) continue;
    return lines[j].kind === "item" ? j : -1;
  }
  return -1;
}

export function canIndent(lines: OutlineLine[], i: number): boolean {
  const before = itemBefore(lines, i);
  return lines[i].start && lines[i].kind === "item" && before >= 0 && lines[i].indent <= lines[before].indent;
}

export function canOutdent(lines: OutlineLine[], i: number): boolean {
  return lines[i].start && lines[i].kind === "item" && lines[i].indent > 0;
}

/** One change to a row: a level in or out, what it is, or whether the line starts a row at all. */
export function changeLine(lines: OutlineLine[], i: number, change: { indent?: 1 | -1; kind?: RowKind; start?: boolean }): OutlineLine[] {
  const next = lines.map((l) => ({ ...l }));
  const l = next[i];
  if (change.indent === 1 && canIndent(lines, i)) l.indent += 1;
  if (change.indent === -1 && canOutdent(lines, i)) l.indent -= 1;
  if (change.kind) l.kind = change.kind;
  if (change.start !== undefined && i > 0) {
    l.start = change.start;
    if (change.start) { const b = itemBefore(next, i); l.kind = "item"; l.indent = b >= 0 ? next[b].indent : 0; }
  }
  return settle(next);
}

/** The rows as shown: each starting line with the lines that carry it on. */
export function outlineRows(lines: OutlineLine[]): { at: number; line: OutlineLine; more: number[] }[] {
  const rows: { at: number; line: OutlineLine; more: number[] }[] = [];
  lines.forEach((l, i) => {
    if (l.start || !rows.length) rows.push({ at: i, line: l, more: [] });
    else rows[rows.length - 1].more.push(i);
  });
  return rows;
}

export function outlineSummary(lines: OutlineLine[]): string {
  const rows = lines.filter((l) => l.start);
  const items = rows.filter((l) => l.kind === "item");
  const nested = items.filter((l) => l.indent > 0).length;
  const heads = rows.filter((l) => l.kind.startsWith("H")).length, paras = rows.filter((l) => l.kind === "P").length;
  const n = (count: number, one: string, many = one + "s") => `${count} ${count === 1 ? one : many}`;
  return [heads ? n(heads, "heading") : "", paras ? n(paras, "paragraph") : "",
    items.length ? `${n(items.length, "list item")}${nested ? ` (${nested} nested)` : ""}` : ""].filter(Boolean).join(", ") || "Nothing to build";
}

/** The edit: `els` in reading order, the first holding the result's place. */
export function outlineEdit(els: TagElement[], lines: OutlineLine[]): TagEdit {
  const [first, ...rest] = els;
  const rows = settle(lines).filter((l) => l.start).map((l): ListRow => ({
    ...(l.el === first.i ? {} : { el: l.el }), at: l.k, ...(l.kind === "item" ? { level: l.indent + 1 } : { as: l.kind }),
  }));
  return { op: "build_list", element: first.i, expect_role: first.role, ...(rest.length ? { absorb: rest.map((e) => e.i) } : {}), rows };
}

/** How a pending build_list will come out, for the tag tree to draw before it's saved: each tag it makes, its
 * depth under the tag's place (a list, its items one deeper, a nested list one deeper again), its type and text. */
export type BuiltRow = { role: string; depth: number; text: string; boxes: { page: number; bbox: [number, number, number, number] }[] };

export type Box = BuiltRow["boxes"][number];
/** One box per page around all of these. */
export function unite(boxes: Box[]): Box[] {
  const pages = new Map<number, [number, number, number, number]>();
  for (const { page, bbox: b } of boxes) {
    const u = pages.get(page);
    pages.set(page, u ? [Math.min(u[0], b[0]), Math.min(u[1], b[1]), Math.max(u[2], b[2]), Math.max(u[3], b[3])] : [...b]);
  }
  return [...pages].map(([page, bbox]) => ({ page, bbox }));
}

export function builtPreview(map: TagMap, op: Extract<TagEdit, { op: "build_list" }>): BuiltRow[] {
  const els = [op.element, ...(op.absorb ?? [])].map((i) => map.elements[i]).filter(Boolean);
  // Every piece of the tags' content in order, as the engine takes them
  const pieces = els.flatMap((e) => (e.parts?.length ? e.parts : [{ k: 0, kind: "content" as const, text: e.text, page: undefined, bbox: undefined }])
    .map((p) => ({ el: e.i, k: p.k, text: p.text ?? "", box: p.page !== undefined && p.bbox ? { page: p.page, bbox: p.bbox } : null })));
  const starts = op.rows.map((r) => pieces.findIndex((p) => p.el === (r.el ?? op.element) && p.k === r.at));
  const out: BuiltRow[] = [];
  let open = 0; // lists open at this point
  op.rows.forEach((row, n) => {
    const from = starts[n], to = n + 1 < starts.length ? starts[n + 1] : pieces.length;
    const mine = from < 0 ? [] : pieces.slice(from, to);
    const text = mine.map((p) => p.text.trim()).filter(Boolean).join(" ");
    const boxes = unite(mine.flatMap((p) => (p.box ? [p.box] : [])));
    if (row.level === undefined) {
      out.push({ role: row.as ?? "P", depth: 0, text, boxes });
      open = 0;
      return;
    }
    const level = Math.min(row.level, open + 1);
    if (level > open) out.push({ role: "L", depth: 2 * (level - 1), text: "", boxes: [] }); // a new list, at the top or under the item before
    open = level;
    out.push({ role: "LI", depth: 2 * level - 1, text, boxes });
  });
  // A list's outline goes round its items and the lists nested in them
  out.forEach((row, n) => {
    if (row.role !== "L") return;
    const inside: Box[] = [];
    for (let m = n + 1; m < out.length && out[m].depth > row.depth; m++) inside.push(...out[m].boxes);
    row.boxes = unite(inside);
  });
  return out;
}

/** What a pending build_list makes, in words, for a screen reader on the one row it is read from. */
export function builtSummary(rows: BuiltRow[]): string {
  if (rows.some((r) => r.role === "Table")) {
    // A table made or edited (#370)
    const trs = rows.filter((r) => r.role === "TR").length;
    const cells = rows.filter((r) => r.role === "TH" || r.role === "TD");
    const heads = cells.filter((r) => r.role === "TH").length;
    return `makes a table of ${trs} row${trs === 1 ? "" : "s"} and ${cells.length} cell${cells.length === 1 ? "" : "s"}${heads ? `, ${heads} of them headers` : ""}`;
  }
  const items = rows.filter((r) => r.role === "LI");
  const nested = items.filter((r) => r.depth > 1).length;
  const others = rows.filter((r) => r.role !== "LI" && r.role !== "L").length;
  return `builds ${others ? `${others} heading or paragraph${others === 1 ? "" : "s"} and ` : ""}a list of ${items.length} item${items.length === 1 ? "" : "s"}${nested ? `, ${nested} of them nested` : ""}`;
}
