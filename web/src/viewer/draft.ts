// Pending tag edits: the list sent to the server, and how the right pane shows them before saving.
import type { TagEdit, TagElement, TagMap } from "../api";

export type Overrides = { role?: string; alt?: string; actual?: string; lang?: string; scope?: string; decorative?: boolean };

/** One pending edit per element and property; moves accumulate in order. */
function key(op: TagEdit): string | null {
  switch (op.op) {
    case "set_role":
    case "set_cell":
      return `role:${op.element}`;
    case "set_alt":
      return `alt:${op.element}`;
    case "set_actual_text":
      return `actual:${op.element}`;
    case "set_decorative":
      return `decorative:${op.element}`;
    case "set_lang":
      return `lang:${op.element}`;
    case "move":
    case "merge":
    case "split":
      return null;
  }
}

/** Tags merged into another by pending edits: absorbed element -> the tag that now holds its content. */
export function absorbedBy(ops: TagEdit[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const op of ops) {
    if (op.op !== "merge") continue;
    for (const a of op.absorb) out.set(a, op.element);
  }
  // Follow chains: if B joined A and A later joined C, B's content ends up in C
  for (const [a] of out) {
    let s = out.get(a)!;
    const seen = new Set([a]);
    while (out.has(s) && !seen.has(s)) { seen.add(s); s = out.get(s)!; }
    out.set(a, s);
  }
  return out;
}

/** The tag that holds an element's content once pending merges are applied. */
export function survivor(ops: TagEdit[], i: number): number {
  return absorbedBy(ops).get(i) ?? i;
}

export function addEdit(ops: TagEdit[], op: TagEdit): TagEdit[] {
  const k = key(op);
  if (k === null) return [...ops, op];
  const i = ops.findIndex((o) => key(o) === k);
  if (i === -1) return [...ops, op];
  const next = ops.slice();
  next[i] = op;
  return next;
}

export function overrides(ops: TagEdit[]): Map<number, Overrides> {
  const out = new Map<number, Overrides>();
  const at = (i: number) => {
    let o = out.get(i);
    if (!o) out.set(i, (o = {}));
    return o;
  };
  for (const op of ops) {
    if (op.op === "set_role") at(op.element).role = op.role;
    else if (op.op === "set_cell") Object.assign(at(op.element), { role: op.role, scope: op.role === "TH" ? op.scope : undefined });
    else if (op.op === "set_alt") at(op.element).alt = op.alt;
    else if (op.op === "set_actual_text") at(op.element).actual = op.text;
    else if (op.op === "set_lang") at(op.element).lang = op.lang;
    else if (op.op === "set_decorative") at(op.element).decorative = true;
  }
  return out;
}

/** Elements with their own content on a page, in reading order, with pending moves applied. */
export function pageOrder(map: TagMap, page: number, ops: TagEdit[]): TagElement[] {
  const gone = absorbedBy(ops);
  const list = map.elements
    .filter((e) => e.own && !gone.has(e.i) && e.boxes.some((b) => b.page === page))
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  for (const op of ops) {
    if (op.op !== "move") continue;
    const from = list.findIndex((e) => e.i === op.element);
    if (from === -1 || list.findIndex((e) => e.i === op.target) === -1) continue;
    const [moved] = list.splice(from, 1);
    const to = list.findIndex((e) => e.i === op.target);
    list.splice(op.position === "before" ? to : to + 1, 0, moved);
  }
  return list;
}

export function describe(op: TagEdit, map: TagMap): string {
  const el = map.elements[op.element];
  const what = el ? `${el.role}${el.text ? ` “${el.text.slice(0, 40)}${el.text.length > 40 ? "…" : ""}”` : ""}` : `element ${op.element}`;
  const page = el?.page ? ` (page ${el.page})` : "";
  switch (op.op) {
    case "set_role":
      return `${what} → ${op.role}${page}`;
    case "set_cell":
      return `${what} → ${op.role}${op.role === "TH" ? `, scope ${op.scope}` : ""}${page}`;
    case "set_alt":
      return op.alt.trim() ? `Alt text for ${what}${page}` : `Alt text removed from ${what}${page}`;
    case "set_actual_text":
      return op.text.trim() ? `Read-aloud text for ${what}${page}` : `Read-aloud text removed from ${what}${page}`;
    case "set_decorative":
      return `${what} marked decorative${page}`;
    case "set_lang":
      return op.lang ? `${what} language → ${op.lang}${page}` : `Language removed from ${what}${page}`;
    case "move": {
      const t = map.elements[op.target];
      return `Moved ${what} ${op.position} ${t ? t.role : "element"}${t?.text ? ` “${t.text.slice(0, 30)}…”` : ""}${page}`;
    }
    case "merge":
      return `Merged ${op.absorb.length} tag${op.absorb.length > 1 ? "s" : ""} into ${what}${page}`;
    case "split": {
      const text = el?.parts?.find((p) => p.k === op.at)?.text ?? (op.char != null ? el?.text : undefined);
      if (op.char != null) return `Split ${what} before “${(text ?? "").slice(op.char, op.char + 30)}”${page}`;
      return `Split ${what} before “${(text ?? "item " + op.at).slice(0, 30)}”${page}`;
    }
  }
}

// Standard structure types, most-used first, for the role picker.
export const ROLE_GROUPS: { label: string; roles: string[] }[] = [
  { label: "Text", roles: ["P", "H1", "H2", "H3", "H4", "H5", "H6", "Caption", "Quote", "BlockQuote", "Note", "Code", "Span"] },
  { label: "Figures", roles: ["Figure", "Formula"] },
  { label: "Lists", roles: ["L", "LI", "Lbl", "LBody"] },
  { label: "Tables", roles: ["Table", "THead", "TBody", "TFoot", "TR", "TH", "TD"] },
  { label: "Links and references", roles: ["Link", "Reference", "BibEntry", "TOC", "TOCI", "Index"] },
  { label: "Grouping", roles: ["Document", "Part", "Art", "Sect", "Div", "NonStruct", "Private", "Form"] },
];

export const ROLE_NAMES: Record<string, string> = {
  P: "Paragraph", H1: "Heading 1", H2: "Heading 2", H3: "Heading 3", H4: "Heading 4", H5: "Heading 5", H6: "Heading 6",
  Figure: "Figure", Formula: "Formula", L: "List", LI: "List item", Lbl: "List label", LBody: "List item body",
  Table: "Table", TR: "Table row", TH: "Header cell", TD: "Data cell", Link: "Link", Caption: "Caption",
  Sect: "Section", Div: "Division", Document: "Document", Span: "Span", Note: "Note", Quote: "Quote",
};

export function roleClass(role: string): string {
  if (/^H[1-6]?$/.test(role)) return "r-heading";
  if (role === "Figure" || role === "Formula") return "r-figure";
  if (["Table", "TR", "TH", "TD", "THead", "TBody", "TFoot"].includes(role)) return "r-table";
  if (["L", "LI", "Lbl", "LBody"].includes(role)) return "r-list";
  if (role === "Link" || role === "Reference") return "r-link";
  return "r-text";
}
