// The table editor's grid (#370): a table's cells by row and column, with their spans, and what can be done to it:
// merge and split cells, add and remove rows and columns, choose the header rows and columns. A removed row's or
// column's content joins its neighbour (above, or to the left; below or to the right for the first), so nothing on
// the page goes untagged. The grid is turned into one edit for the engine: rebuild_table for a table, build_table
// for text tags made into one.
import type { TagEdit, TagElement, TagMap } from "../api";
import { unite, type BuiltRow } from "./outline";

/** A piece of a text tag that a cell can hold: /K item `at` of tag `el`. */
export type PieceRef = { el: number; at: number };

export type GridCell = {
  key: number;           // this grid's own name for the cell
  r: number; c: number;  // where it starts
  rows: number; cols: number;
  role: "TH" | "TD";
  scope?: "Row" | "Column" | "Both";
  from: number[];        // the old cells it takes the content of (a table being edited)
  pieces: PieceRef[];    // or the pieces of text it holds (text tags being made a table)
  text: string;          // what it says, for the grid
};

export type Grid = { cells: GridCell[]; height: number; width: number; caption?: number | null; next: number };

const SCOPES = new Set(["Row", "Column", "Both"]);

/** Which cell covers each place: grid[r][c] is a cell's key (or -1 where nothing does, which a valid grid hasn't). */
export function slots(g: Grid): number[][] {
  const out = Array.from({ length: g.height }, () => Array<number>(g.width).fill(-1));
  for (const x of g.cells) {
    for (let i = 0; i < x.rows; i++) for (let j = 0; j < x.cols; j++) {
      if (out[x.r + i]?.[x.c + j] !== undefined) out[x.r + i][x.c + j] = x.key;
    }
  }
  return out;
}

function sorted(cells: GridCell[]): GridCell[] {
  return [...cells].sort((a, b) => a.r - b.r || a.c - b.c);
}

/** A table from the tag map: its rows (directly, or inside THead, TBody, TFoot) and their cells with spans, laid
 * out as HTML lays out a table. */
export function fromTable(map: TagMap, table: number): Grid {
  const els = map.elements;
  const t = els[table];
  const rows: TagElement[] = [];
  for (const k of t?.kids ?? []) {
    const e = els[k];
    if (e?.role === "TR") rows.push(e);
    else if (e && ["THead", "TBody", "TFoot"].includes(e.role)) rows.push(...e.kids.map((r) => els[r]).filter((r) => r?.role === "TR"));
  }
  const taken = new Set<string>();
  const cells: GridCell[] = [];
  let key = 0;
  rows.forEach((tr, r) => {
    let c = 0;
    for (const k of tr.kids) {
      const e = els[k];
      if (!e || (e.role !== "TH" && e.role !== "TD")) continue;
      while (taken.has(`${r},${c}`)) c++;
      const [rs, cs] = e.span ?? [1, 1];
      for (let i = 0; i < rs; i++) for (let j = 0; j < cs; j++) taken.add(`${r + i},${c + j}`);
      cells.push({ key: key++, r, c, rows: rs, cols: cs, role: e.role, scope: e.role === "TH" ? (SCOPES.has(e.scope ?? "") ? e.scope as GridCell["scope"] : "Column") : undefined,
        from: [e.i], pieces: [], text: e.text ?? "" });
      c += cs;
    }
  });
  const height = Math.max(rows.length, ...cells.map((x) => x.r + x.rows));
  const width = Math.max(0, ...cells.map((x) => x.c + x.cols));
  const caption = t?.kids.find((k) => els[k]?.role === "Caption");
  return fill({ cells, height, width, caption: caption ?? null, next: key });
}

/** The grid a pending rebuild_table describes, so the editor reopens where it was left. */
export function fromOp(map: TagMap, op: Extract<TagEdit, { op: "rebuild_table" }>): Grid {
  const text = (ids: number[]) => ids.map((i) => map.elements[i]?.text ?? "").filter(Boolean).join(" ");
  const g: Grid = { cells: [], height: op.rows.length, width: 0, caption: op.caption === undefined ? fromTable(map, op.element).caption : op.caption, next: 0 };
  const taken = new Set<string>();
  op.rows.forEach((row, r) => {
    let c = 0;
    for (const s of row) {
      while (taken.has(`${r},${c}`)) c++;
      const rs = s.rows ?? 1, cs = s.cols ?? 1;
      for (let i = 0; i < rs; i++) for (let j = 0; j < cs; j++) taken.add(`${r + i},${c + j}`);
      g.cells.push({ key: g.next++, r, c, rows: rs, cols: cs, role: s.role, scope: s.role === "TH" ? s.scope ?? "Column" : undefined,
        from: [...s.from], pieces: [], text: text(s.from) });
      g.width = Math.max(g.width, c + cs);
      c += cs;
    }
  });
  return fill(g);
}

/** Empty cells wherever nothing covers a place (a table whose rows were uneven), so every grid is a rectangle. */
function fill(g: Grid): Grid {
  const s = slots(g);
  const cells = [...g.cells];
  let next = g.next;
  s.forEach((row, r) => row.forEach((k, c) => {
    if (k === -1) cells.push({ key: next++, r, c, rows: 1, cols: 1, role: "TD", from: [], pieces: [], text: "" });
  }));
  return { ...g, cells: sorted(cells), next };
}

/** Text tags as a table, from where their pieces sit on the page: rows by line (pieces whose middles share a
 * line), columns by where pieces start (starts within a few points are one column). A piece is a whole /K item, so
 * a line drawn as one piece stays in one cell. Pieces with no place of their own (a drawn grid's ruled lines, in a
 * figure that is really a table) don't shape the grid: they go in the first cell, where screen readers skip them. */
export function fromLayout(map: TagMap, tags: number[]): Grid {
  type P = { ref: PieceRef; x0: number; y0: number; x1: number; y1: number; text: string; order: number };
  const pieces: P[] = [];
  const loose: PieceRef[] = [];
  for (const i of tags) {
    const e = map.elements[i];
    if (!e) continue;
    const parts = e.parts?.filter((p) => p.kind === "content")
      ?? [{ k: 0, kind: "content" as const, bbox: e.boxes[0]?.bbox, text: e.text ?? undefined }];
    for (const p of parts) {
      const b = p.bbox;
      if (!b) { loose.push({ el: i, at: p.k }); continue; }
      pieces.push({ ref: { el: i, at: p.k }, x0: b[0], y0: b[1], x1: b[2], y1: b[3], text: p.text ?? "", order: pieces.length });
    }
  }
  // Rows: a piece joins the row whose band its middle falls in
  const bands: { y0: number; y1: number; items: P[] }[] = [];
  for (const p of [...pieces].sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0)) {
    const mid = (p.y0 + p.y1) / 2;
    const band = bands.find((b) => mid >= b.y0 && mid <= b.y1);
    if (band) { band.items.push(p); band.y0 = Math.min(band.y0, p.y0); band.y1 = Math.max(band.y1, p.y1); }
    else bands.push({ y0: p.y0, y1: p.y1, items: [p] });
  }
  // Columns: starts within 8 points of each other
  const starts = [...pieces].map((p) => p.x0).sort((a, b) => a - b);
  const cols: number[] = [];
  for (const x of starts) if (!cols.length || x - cols[cols.length - 1] > 8) cols.push(x);
  const colOf = (x: number) => { let k = 0; for (let j = 0; j < cols.length; j++) if (x >= cols[j] - 4) k = j; return k; };
  const g: Grid = { cells: [], height: bands.length, width: Math.max(1, cols.length), next: 0 };
  if (!bands.length) bands.push({ y0: 0, y1: 0, items: [] });  // nothing placed: one empty row to start from
  bands.forEach((band, r) => {
    const by = new Map<number, P[]>();
    for (const p of band.items) by.set(colOf(p.x0), [...(by.get(colOf(p.x0)) ?? []), p]);
    for (const [c, ps] of by) {
      ps.sort((a, b) => a.order - b.order);
      g.cells.push({ key: g.next++, r, c, rows: 1, cols: 1, role: r === 0 ? "TH" : "TD", scope: r === 0 ? "Column" : undefined,
        from: [], pieces: ps.map((p) => p.ref), text: ps.map((p) => p.text).join(" ") });
    }
  });
  const out = fill(g);
  if (loose.length) {
    const first = out.cells.find((x) => x.r === 0 && x.c === 0)!;
    first.pieces = [...first.pieces, ...loose];
  }
  return out;
}

function cellAt(g: Grid, r: number, c: number): GridCell | undefined {
  return g.cells.find((x) => r >= x.r && r < x.r + x.rows && c >= x.c && c < x.c + x.cols);
}

function joined(into: GridCell, other: GridCell, before = false): GridCell {
  const [a, b] = before ? [other, into] : [into, other];  // what comes first in reading order
  return { ...into, from: [...a.from, ...b.from], pieces: [...a.pieces, ...b.pieces],
    text: [a.text, b.text].filter(Boolean).join(" ") };
}

/** The cell to its right (or below) joins it, when it's as tall (or as wide) and lines up. */
export function canMerge(g: Grid, key: number, way: "right" | "down"): boolean {
  const x = g.cells.find((y) => y.key === key);
  if (!x) return false;
  const n = way === "right" ? cellAt(g, x.r, x.c + x.cols) : cellAt(g, x.r + x.rows, x.c);
  if (!n) return false;
  return way === "right" ? n.r === x.r && n.rows === x.rows : n.c === x.c && n.cols === x.cols;
}

export function merge(g: Grid, key: number, way: "right" | "down"): Grid {
  if (!canMerge(g, key, way)) return g;
  const x = g.cells.find((y) => y.key === key)!;
  const n = (way === "right" ? cellAt(g, x.r, x.c + x.cols) : cellAt(g, x.r + x.rows, x.c))!;
  const big = { ...joined(x, n), ...(way === "right" ? { cols: x.cols + n.cols } : { rows: x.rows + n.rows }) };
  return { ...g, cells: sorted(g.cells.filter((y) => y.key !== n.key).map((y) => (y.key === key ? big : y))) };
}

/** A spanning cell back to one place; the places it covered get empty cells of the same kind. */
export function split(g: Grid, key: number): Grid {
  const x = g.cells.find((y) => y.key === key);
  if (!x || (x.rows === 1 && x.cols === 1)) return g;
  let next = g.next;
  const added: GridCell[] = [];
  for (let i = 0; i < x.rows; i++) for (let j = 0; j < x.cols; j++) {
    if (i || j) added.push({ key: next++, r: x.r + i, c: x.c + j, rows: 1, cols: 1, role: x.role, scope: x.scope, from: [], pieces: [], text: "" });
  }
  return { ...g, next, cells: sorted([...g.cells.map((y) => (y.key === key ? { ...y, rows: 1, cols: 1 } : y)), ...added]) };
}

/** A new empty row after row `after` (-1: at the top). A cell spanning across that line grows instead. */
export function addRow(g: Grid, after: number): Grid {
  let next = g.next;
  const cells = g.cells.map((x) => {
    if (x.r > after) return { ...x, r: x.r + 1 };
    if (x.r <= after && x.r + x.rows - 1 > after) return { ...x, rows: x.rows + 1 };
    return x;
  });
  const out = { ...g, cells, height: g.height + 1 };
  const s = slots(out);
  for (let c = 0; c < g.width; c++) {
    if (s[after + 1][c] === -1) cells.push({ key: next++, r: after + 1, c, rows: 1, cols: 1, role: "TD", from: [], pieces: [], text: "" });
  }
  return { ...out, cells: sorted(cells), next };
}

/** A new empty column after column `after` (-1: at the left). A cell spanning across that line grows instead. */
export function addColumn(g: Grid, after: number): Grid {
  let next = g.next;
  const cells = g.cells.map((x) => {
    if (x.c > after) return { ...x, c: x.c + 1 };
    if (x.c <= after && x.c + x.cols - 1 > after) return { ...x, cols: x.cols + 1 };
    return x;
  });
  const out = { ...g, cells, width: g.width + 1 };
  const s = slots(out);
  for (let r = 0; r < g.height; r++) {
    if (s[r][after + 1] === -1) {
      const header = r < headerRows(g);
      cells.push({ key: next++, r, c: after + 1, rows: 1, cols: 1, role: header ? "TH" : "TD", scope: header ? "Column" : undefined, from: [], pieces: [], text: "" });
    }
  }
  return { ...out, cells: sorted(cells), next };
}

/** Row `r` goes: a cell spanning it gets shorter; a cell only in it joins the cell above (below, for the first row). */
export function removeRow(g: Grid, r: number): Grid {
  if (g.height < 2) return g;
  const into = new Map<number, GridCell>();
  const keep: GridCell[] = [];
  for (const x of g.cells) {
    if (x.r + x.rows - 1 < r) keep.push(x);
    else if (x.r > r) keep.push({ ...x, r: x.r - 1 });
    else if (x.rows > 1) keep.push({ ...x, rows: x.rows - 1 });
    else {
      const t = cellAt(g, r === 0 ? r + 1 : r - 1, x.c);
      if (t) into.set(t.key, joined(into.get(t.key) ?? t, x, r === 0));  // the first row's content reads before
    }
  }
  const cells = keep.map((x) => {
    const j = into.get(x.key);
    return j ? { ...x, from: j.from, pieces: j.pieces, text: j.text } : x;
  });
  return { ...g, cells: sorted(cells), height: g.height - 1 };
}

/** Column `c` goes: a cell spanning it gets narrower; a cell only in it joins the cell to its left (right, for the
 * first column). */
export function removeColumn(g: Grid, c: number): Grid {
  if (g.width < 2) return g;
  const into = new Map<number, GridCell>();
  const keep: GridCell[] = [];
  for (const x of g.cells) {
    if (x.c + x.cols - 1 < c) keep.push(x);
    else if (x.c > c) keep.push({ ...x, c: x.c - 1 });
    else if (x.cols > 1) keep.push({ ...x, cols: x.cols - 1 });
    else {
      const t = cellAt(g, x.r, c === 0 ? c + 1 : c - 1);
      if (t) into.set(t.key, joined(into.get(t.key) ?? t, x, c === 0));
    }
  }
  const cells = keep.map((x) => {
    const j = into.get(x.key);
    return j ? { ...x, from: j.from, pieces: j.pieces, text: j.text } : x;
  });
  return { ...g, cells: sorted(cells), width: g.width - 1 };
}

/** How many leading rows (or columns) are all header cells. */
export function headerRows(g: Grid): number {
  let k = 0;
  while (k < g.height && g.cells.filter((x) => x.r <= k && x.r + x.rows > k).every((x) => x.role === "TH")) k++;
  return k;
}
export function headerCols(g: Grid): number {
  let k = 0;
  while (k < g.width && g.cells.filter((x) => x.c <= k && x.c + x.cols > k).every((x) => x.role === "TH")) k++;
  return k;
}

/** The first `rows` rows and `cols` columns are headers (column headers, row headers, the corner both); the rest
 * data cells. */
export function setHeaders(g: Grid, rows: number, cols: number): Grid {
  return { ...g, cells: g.cells.map((x) => {
    const inRows = x.r < rows, inCols = x.c < cols;
    if (!inRows && !inCols) return { ...x, role: "TD", scope: undefined };
    return { ...x, role: "TH", scope: inRows && inCols ? "Both" : inRows ? "Column" : "Row" };
  }) };
}

/** One cell between header and data. */
export function toggleHeader(g: Grid, key: number): Grid {
  return { ...g, cells: g.cells.map((x) => (x.key !== key ? x : x.role === "TH"
    ? { ...x, role: "TD", scope: undefined } : { ...x, role: "TH", scope: x.r < headerRows(g) || x.r === 0 ? "Column" : "Row" })) };
}

/** Is it a rectangle with every place covered once? (What the engine will check.) */
export function problems(g: Grid): string | null {
  const s = slots(g);
  if (s.some((row) => row.some((k) => k === -1))) return "Some places in the table have no cell.";
  const count = new Map<string, number>();
  for (const x of g.cells) for (let i = 0; i < x.rows; i++) for (let j = 0; j < x.cols; j++) {
    const k = `${x.r + i},${x.c + j}`;
    count.set(k, (count.get(k) ?? 0) + 1);
  }
  if ([...count.values()].some((n) => n > 1)) return "Two cells cover the same place.";
  return null;
}

function rowsOf<T>(g: Grid, f: (x: GridCell) => T): T[][] {
  const out: T[][] = Array.from({ length: g.height }, () => []);
  for (const x of sorted(g.cells)) out[x.r].push(f(x));
  return out;
}

function shape(x: GridCell) {
  return { role: x.role, ...(x.role === "TH" ? { scope: x.scope ?? "Column" } : {}),
    ...(x.rows > 1 ? { rows: x.rows } : {}), ...(x.cols > 1 ? { cols: x.cols } : {}) };
}

/** The edit that makes a table what the grid shows. */
export function rebuildEdit(map: TagMap, table: number, g: Grid): Extract<TagEdit, { op: "rebuild_table" }> {
  const original = fromTable(map, table).caption ?? null;
  return { op: "rebuild_table", element: table, expect_role: map.elements[table].role,
    rows: rowsOf(g, (x) => ({ from: x.from, ...shape(x) })),
    ...((g.caption ?? null) !== original ? { caption: g.caption ?? null } : {}) };
}

/** The edit that makes text tags the table the grid shows. */
export function buildEdit(map: TagMap, tags: number[], g: Grid): Extract<TagEdit, { op: "build_table" }> {
  const first = tags[0];
  return { op: "build_table", element: first, expect_role: map.elements[first].role, absorb: tags.slice(1),
    rows: rowsOf(g, (x) => ({ parts: x.pieces.map((p) => (p.el === first ? { at: p.at } : { el: p.el, at: p.at })), ...shape(x) })) };
}

type Box = BuiltRow["boxes"][number];

/** A pending table edit as it will be once saved, for the tag tree and the page (#370): the table, its caption,
 * rows and cells, each cell with its text and the boxes of what it holds. It stands for the tag the edit names
 * (the first text tag, or the table) until it's saved. */
export function tablePreview(map: TagMap, op: Extract<TagEdit, { op: "build_table" | "rebuild_table" }>): BuiltRow[] {
  const els = map.elements;
  const ownBoxes = (e: TagElement | undefined): Box[] => (e ? e.boxes.map((b) => ({ page: b.page, bbox: b.bbox })) : []);
  const cellOf = (spec: { role: string; rows?: number; cols?: number }, text: string, boxes: Box[]): BuiltRow =>
    ({ role: spec.role, depth: 2, text, boxes: unite(boxes) });
  const rows: BuiltRow[][] = [];
  if (op.op === "build_table") {
    const piece = (el: number, at: number): { text: string; boxes: Box[] } => {
      const e = els[el];
      const part = e?.parts?.find((p) => p.k === at);
      if (!e?.parts) return { text: e?.text ?? "", boxes: ownBoxes(e) };
      return { text: part?.text ?? "", boxes: part?.bbox && part.page ? [{ page: part.page, bbox: part.bbox }] : [] };
    };
    for (const row of op.rows) {
      rows.push(row.map((c) => {
        const got = c.parts.map((r) => piece(r.el ?? op.element, r.at));
        return cellOf(c, got.map((g) => g.text.trim()).filter(Boolean).join(" "), got.flatMap((g) => g.boxes));
      }));
    }
  } else {
    for (const row of op.rows) {
      rows.push(row.map((c) => cellOf(c, c.from.map((i) => els[i]?.text ?? "").filter(Boolean).join(" "),
        c.from.flatMap((i) => ownBoxes(els[i])))));
    }
  }
  const out: BuiltRow[] = [];
  const table = els[op.element];
  const oldCaption = op.op === "rebuild_table" ? table?.kids.find((k) => els[k]?.role === "Caption") : undefined;
  const caption = op.op === "rebuild_table" ? (op.caption === undefined ? oldCaption : op.caption ?? undefined) : undefined;
  if (op.op === "rebuild_table" && op.caption === null && oldCaption !== undefined) {
    out.push({ role: "P", depth: 0, text: els[oldCaption]?.text ?? "", boxes: ownBoxes(els[oldCaption]) });  // back out
  }
  const all = rows.flat().flatMap((c) => c.boxes);
  const capBoxes = caption !== undefined ? ownBoxes(els[caption]) : [];
  out.push({ role: "Table", depth: 0, text: "", boxes: unite([...all, ...capBoxes]) });
  if (caption !== undefined) out.push({ role: "Caption", depth: 1, text: els[caption]?.text ?? "", boxes: capBoxes });
  for (const row of rows) {
    out.push({ role: "TR", depth: 1, text: "", boxes: unite(row.flatMap((c) => c.boxes)) });
    out.push(...row);
  }
  return out;
}

/** The tags a pending table edit takes the place of, which are hidden until it's saved: the other text tags made
 * into the table, or the table's own rows and cells (and a tag made its caption). */
export function tableReplaced(map: TagMap, op: Extract<TagEdit, { op: "build_table" | "rebuild_table" }>): number[] {
  if (op.op === "build_table") return op.absorb ?? [];
  const out: number[] = [];
  const stack = [...(map.elements[op.element]?.kids ?? [])];
  while (stack.length) {
    const k = stack.pop()!;
    out.push(k);
    stack.push(...(map.elements[k]?.kids ?? []));
  }
  if (typeof op.caption === "number") out.push(op.caption);
  return out;
}

