// Pending tag edits: the list sent to the server, and how the right pane shows them before saving.
import type { TagEdit, TagElement, TagMap } from "../api";

export type Overrides = { role?: string; alt?: string; actual?: string; lang?: string; scope?: string; decorative?: boolean;
  fieldLabel?: string };

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
    case "delete":
      return `delete:${op.element}`;
    case "split":
    case "assign":
    case "tag_artifacts":
    case "make_list":
    case "indent":
    case "outdent":
    case "build_list":
    case "take_pieces":
    case "cut_area":
      return null;
    case "make_toc":
      return `toc:${op.element}`;
    case "set_headers":
      return `headers:${op.element}`;
    case "link_note":
      return `note:${op.element}`;
    case "set_toc_target":
      return `toctarget:${op.element}`;
    case "set_field_label":
      return `fieldlabel:${op.element}`;
    case "set_doc_title":
      return "doc:title";
    case "set_doc_lang":
      return "doc:lang";
    case "rebuild_table":
      return `table:${op.element}`;  // the editor's grid replaces what it said before
    case "build_table":
      return null;
  }
}

/** The document's own title and language once pending edits are applied (#363): what the Document panel shows. */
export function documentDraft(ops: TagEdit[], map?: TagMap | null): { title: string; lang: string } {
  let title = map?.document?.title ?? "", lang = map?.document?.lang ?? "";
  for (const op of ops) {
    if (op.op === "set_doc_title") title = op.title;
    else if (op.op === "set_doc_lang") lang = op.lang;
  }
  return { title, lang };
}

/** Tags merged into another by pending edits: absorbed element -> the tag that now holds its content. */
export function absorbedBy(ops: TagEdit[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const op of ops) {
    if (op.op !== "merge" && op.op !== "build_list") continue; // a list built from several tags takes their content too
    for (const a of op.absorb ?? []) out.set(a, op.element);
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

const TOC_ROLES: Record<string, string> = { L: "TOC", LI: "TOCI", LBody: "Reference" };

/** Pending changes to each tag's own properties. With the tag map, a list being made a table of contents shows its
 * new types too (TOC, TOCI, Reference), the way the engine will write them. */
export function overrides(ops: TagEdit[], map?: TagMap | null): Map<number, Overrides> {
  const out = new Map<number, Overrides>();
  const at = (i: number) => {
    let o = out.get(i);
    if (!o) out.set(i, (o = {}));
    return o;
  };
  const toc = (i: number) => {
    const e = map?.elements[i];
    if (!e) return;
    const role = TOC_ROLES[out.get(i)?.role ?? e.role];
    if (role) at(i).role = role;
    if (role) for (const k of e.kids) toc(k);
  };
  for (const op of ops) {
    if (op.op === "make_toc") toc(op.element);
    else if (op.op === "set_role") at(op.element).role = op.role;
    else if (op.op === "set_cell") Object.assign(at(op.element), { role: op.role, scope: op.role === "TH" ? op.scope : undefined });
    else if (op.op === "set_alt") at(op.element).alt = op.alt;
    else if (op.op === "set_actual_text") at(op.element).actual = op.text;
    else if (op.op === "set_lang") at(op.element).lang = op.lang;
    else if (op.op === "set_decorative") at(op.element).decorative = true;
    else if (op.op === "set_field_label") at(op.element).fieldLabel = op.label;
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
  const deleted = deletedTags(map, ops);
  return deleted.size ? list.filter((e) => !deleted.has(e.i)) : list;
}

/** Tags that pending deletions remove: each deleted tag, and (when it's deleted with what's inside) everything in it. */
export function deletedTags(map: TagMap, ops: TagEdit[]): Set<number> {
  return ops.some((o) => o.op === "delete") ? draftTree(map, ops).deleted : new Set();
}

const ARTIFACT_WORDS: Record<string, string> = { layout: "decorative", header: "a running page header",
  footer: "a running page footer", page_number: "a page number" };

export function describe(op: TagEdit, map: TagMap): string {
  if (op.op === "set_doc_title") return `Document title → “${op.title.slice(0, 60)}”`;
  if (op.op === "set_doc_lang") return `Document language → ${op.lang}`;
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
      return `${what} marked ${ARTIFACT_WORDS[op.as ?? "layout"]}${page}`;
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
    case "delete":
      return op.children === "lift" ? `Removed the tag ${what}; what was inside moves up a level${page}`
        : `Deleted ${what} and everything in it${page}`;
    case "assign":
      return `${op.pieces.length} piece${op.pieces.length > 1 ? "s" : ""} of content moved into ${what}${page}`;
    case "cut_area":
      return `The area drawn on page ${op.page} cut out of ${what} into a new ${ROLE_NAMES[op.role] ?? op.role}${op.alt ? ` (“${op.alt.slice(0, 40)}”)` : ""}`;
    case "take_pieces":
      return `${op.pieces.length} piece${op.pieces.length > 1 ? "s" : ""} of ${what} made into a new ${ROLE_NAMES[op.role] ?? op.role}${op.alt ? ` (“${op.alt.slice(0, 40)}”)` : ""}${page}`;
    case "build_list": {
      const items = op.rows.filter((r) => r.level !== undefined).length, others = op.rows.length - items;
      return `${what} built into ${items} list item${items === 1 ? "" : "s"}${others ? ` and ${others} other tag${others === 1 ? "" : "s"}` : ""}${page}`;
    }
    case "make_list":
      return `One list made of ${op.absorb.length + 1} tags, starting with ${what}${page}`;
    case "indent":
      return `${what} moved one level in, under the list item before it${page}`;
    case "outdent":
      return `${what} moved one level out of its nested list${page}`;
    case "make_toc":
      return `${what} made into a table of contents${page}`;
    case "set_field_label":
      return `Field label for ${what} → “${op.label.slice(0, 40)}”${page}`;
    case "rebuild_table": {
      const width = Math.max(0, ...op.rows.map((r) => r.reduce((n, c) => n + (c.cols ?? 1), 0)));
      return `${what} edited: ${op.rows.length} row${op.rows.length === 1 ? "" : "s"} by ${width} column${width === 1 ? "" : "s"}${op.caption === null ? ", caption removed" : op.caption !== undefined ? ", with a caption" : ""}${page}`;
    }
    case "build_table": {
      const width = Math.max(0, ...op.rows.map((r) => r.reduce((n, c) => n + (c.cols ?? 1), 0)));
      return `${what}${op.absorb?.length ? ` and ${op.absorb.length} more` : ""} made into a table of ${op.rows.length} row${op.rows.length === 1 ? "" : "s"} by ${width} column${width === 1 ? "" : "s"}${page}`;
    }
    case "link_note":
      return op.note === null ? `${what} no longer points at a footnote${page}`
        : `${what} points at the footnote “${(map.elements[op.note]?.text ?? `element ${op.note}`).slice(0, 30)}”${page}`;
    case "set_toc_target":
      return op.target === null ? `${what} goes to no heading${page}`
        : `${what} goes to the heading “${(map.elements[op.target]?.text ?? `element ${op.target}`).slice(0, 30)}”${page}`;
    case "set_headers": {
      const names = op.headers.map((h) => `“${(map.elements[h]?.text ?? `element ${h}`).slice(0, 20)}”`).join(", ");
      return op.headers.length ? `${what} read with ${names}${page}` : `Headers removed from ${what}${page}`;
    }
    case "tag_artifacts":
      return `New ${op.role} from ${op.sections.length} untagged item${op.sections.length > 1 ? "s" : ""} on page ${op.page}, ${op.position} ${what}`;
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
  Sect: "Section", Div: "Division", Document: "Document", Span: "Span", Note: "Note", FENote: "Note", Quote: "Quote",
};

export function roleClass(role: string): string {
  if (/^H[1-6]?$/.test(role)) return "r-heading";
  if (role === "Figure" || role === "Formula") return "r-figure";
  if (["Table", "TR", "TH", "TD", "THead", "TBody", "TFoot"].includes(role)) return "r-table";
  if (["L", "LI", "Lbl", "LBody"].includes(role)) return "r-list";
  if (role === "Link" || role === "Reference") return "r-link";
  return "r-text";
}

/** The tags under `element` that are a link or a form field (the outermost of each), in reading order. Deleting
 * `element` with everything in it keeps these: they move up into its parent. */
export function keptHolders(map: TagMap, kids: Map<number, number[]>, element: number): number[] {
  const out: number[] = [];
  const walk = (i: number) => {
    for (const k of kids.get(i) ?? []) {
      if (map.elements[k]?.annot) out.push(k);
      else walk(k);
    }
  };
  walk(element);
  return out;
}

/** The tag tree with pending moves and deletions applied: each element's children, its parent, and the tags that
 * deletions remove. Merged-away tags are left out. */
export function draftTree(map: TagMap, ops: TagEdit[]): { kids: Map<number, number[]>; parent: Map<number, number | null>; deleted: Set<number> } {
  const gone = absorbedBy(ops);
  const kids = new Map<number, number[]>();
  const parent = new Map<number, number | null>();
  for (const e of map.elements) {
    kids.set(e.i, e.kids.filter((k) => map.elements[k] && !gone.has(k)));
    parent.set(e.i, e.parent);
  }
  const roots = map.elements.filter((e) => e.parent === null).map((e) => e.i);
  const deleted = new Set<number>();
  for (const op of ops) {
    if (op.op === "delete") {
      const up = parent.get(op.element);
      if (up === undefined || deleted.has(op.element)) continue;
      const siblings = up === null ? roots : kids.get(up)!;
      const at = siblings.indexOf(op.element);
      const inside = kids.get(op.element) ?? [];
      if (op.children === "lift") {
        // Its child tags take its place under its parent
        if (at >= 0) siblings.splice(at, 1, ...inside);
        for (const k of inside) parent.set(k, up);
      } else {
        // Links and form fields have to stay tagged: each is kept whole and takes the deleted tag's place
        const kept = keptHolders(map, kids, op.element);
        if (at >= 0) siblings.splice(at, 1, ...kept);
        for (const k of kept) parent.set(k, up);
        const keep = new Set(kept);
        const stack = inside.filter((k) => !keep.has(k));
        while (stack.length) {
          const k = stack.pop()!;
          deleted.add(k);
          const under = kids.get(k) ?? [];
          stack.push(...under.filter((x) => !keep.has(x)));
          kids.set(k, under.filter((x) => !keep.has(x)));
        }
      }
      kids.set(op.element, []);
      deleted.add(op.element);
      continue;
    }
    if (op.op !== "move") continue;
    const from = parent.get(op.element);
    const to = parent.get(op.target);
    if (from === undefined || to === undefined) continue;
    const take = (p: number | null) => p === null ? roots : kids.get(p)!;
    const src = take(from);
    const at0 = src.indexOf(op.element);
    if (at0 >= 0) src.splice(at0, 1);
    const dst = take(to);
    const at = dst.indexOf(op.target);
    if (at < 0) continue;
    dst.splice(op.position === "before" ? at : at + 1, 0, op.element);
    parent.set(op.element, to);
  }
  kids.set(-1, roots);
  return { kids, parent, deleted };
}

export type Area = { page: number; bbox: [number, number, number, number] };

/** Content and artifacts already used by pending area edits (so they aren't offered twice). */
export function pendingArea(ops: TagEdit[]): { pieces: Map<string, number>; sections: Set<string> } {
  const pieces = new Map<string, number>();
  const sections = new Set<string>();
  for (const op of ops) {
    if (op.op === "assign") for (const p of op.pieces) pieces.set(`${p.page}:${p.mcid}`, op.element);
    if (op.op === "take_pieces") for (const p of op.pieces) pieces.set(`${p.page}:${p.mcid}`, -1); // into a tag not made yet
    if (op.op === "tag_artifacts") for (const s of op.sections) sections.add(`${op.page}:${s}`);
  }
  return { pieces, sections };
}

/** Share of box b inside the area. */
export function inside(area: Area["bbox"], b: [number, number, number, number]): number {
  const w = Math.max(0, Math.min(area[2], b[2]) - Math.max(area[0], b[0]));
  const h = Math.max(0, Math.min(area[3], b[3]) - Math.max(area[1], b[1]));
  const bw = b[2] - b[0], bh = b[3] - b[1];
  // Lines have no width or height: count how much of their length is inside
  if (bw <= 0.5 && bh <= 0.5) return area[0] <= b[0] && b[2] <= area[2] && area[1] <= b[1] && b[3] <= area[3] ? 1 : 0;
  if (bw <= 0.5) return area[0] - 0.5 <= b[0] && b[2] <= area[2] + 0.5 ? h / bh : 0;
  if (bh <= 0.5) return area[1] - 0.5 <= b[1] && b[3] <= area[3] + 0.5 ? w / bw : 0;
  return (w * h) / (bw * bh);
}

/** What pending edits do to each tag, in a word or two per edit ("moved", "type changed"), for marking it in the tree. */
export function editMarks(map: TagMap, ops: TagEdit[]): Map<number, string[]> {
  const out = new Map<number, string[]>();
  const add = (i: number, what: string) => {
    const l = out.get(i) ?? [];
    if (!l.includes(what)) l.push(what);
    out.set(i, l);
  };
  const owner = new Map((map.pieces ?? []).map((p) => [`${p.page}:${p.mcid}`, p.el]));
  let levels: ListPlan | null = null;
  for (const op of ops) {
    switch (op.op) {
      case "set_role": case "set_cell": add(op.element, "type changed"); break;
      case "set_alt": add(op.element, "alt text"); break;
      case "set_actual_text": add(op.element, "read-aloud text"); break;
      case "set_lang": add(op.element, "language"); break;
      case "set_decorative": add(op.element, "decorative"); break;
      case "move": add(op.element, "moved"); break;
      case "merge": add(op.element, "merged"); break;
      case "split": add(op.element, "split"); break;
      case "assign":
        add(op.element, "content added");
        for (const p of op.pieces) {
          const from = owner.get(`${p.page}:${p.mcid}`);
          if (from !== undefined && from !== op.element) add(from, "content removed");
        }
        break;
      case "tag_artifacts": add(op.element, `new ${op.role} ${op.position} it`); break;
      case "build_list": add(op.element, "list built"); break;
      case "take_pieces": case "cut_area": add(op.element, `split: new ${ROLE_NAMES[op.role] ?? op.role}`); break;
      case "make_list": for (const i of [op.element, ...op.absorb]) add(i, map.elements[i]?.role === "L" ? "joined a list" : "list item"); break;
      case "make_toc": add(op.element, "table of contents"); break;
      case "set_headers": add(op.element, "headers"); break;
      case "link_note": add(op.element, op.note === null ? "footnote link removed" : "points at a footnote"); break;
      case "set_toc_target": add(op.element, op.target === null ? "goes to no heading" : "goes to a heading"); break;
      case "set_field_label": add(op.element, "field label"); break;
      case "rebuild_table": add(op.element, "table edited"); break;
      case "build_table": for (const i of [op.element, ...(op.absorb ?? [])]) add(i, "made into a table"); break;
      case "indent": case "outdent": {
        const level = (levels ??= listPlan(map, ops)).level(op.element);
        add(op.element, level ? `list level ${level}` : op.op === "indent" ? "one level in" : "one level out");
        break;
      }
      case "delete": {
        // The tag itself is gone from the tree: the mark goes on what held it
        const up = map.elements[op.element]?.parent;
        if (up !== null && up !== undefined) add(up, op.children === "lift" ? "tag removed" : "tag deleted");
        break;
      }
    }
  }
  return out;
}

// --- lists and their levels, with pending edits applied -----------------------------------------------

type PlanList = { items: PlanItem[]; under: PlanItem | null; tail: boolean };
type PlanItem = { el: number; list: PlanList; sub: PlanList | null };

export type ListPlan = {
  /** How deep a list item is (1 = a top-level item), or null when the tag isn't a list item. */
  level: (el: number) => number | null;
  /** The list item a tag is, or is part of (an item's label, body or text), or null. */
  itemOf: (el: number) => number | null;
  /** Pending indents and outdents that can't be done, by their place in the edits, with the reason. */
  bad: Map<number, string>;
};

/** The file's lists with the pending make_list, indent and outdent edits applied in order, the way the engine
 * applies them: enough to say which of those edits will work and how deep each item ends up. Lists nested
 * directly in a list (L > L, which some producers write) count as top level here; the engine handles them. */
export function listPlan(map: TagMap, ops: TagEdit[]): ListPlan {
  const els = map.elements;
  const items = new Map<number, PlanItem>();
  const lists = new Map<number, PlanList>();
  const listOf = (i: number) => {
    let l = lists.get(i);
    if (!l) lists.set(i, (l = { items: [], under: null, tail: true }));
    return l;
  };
  for (const e of els) {
    if (e.role !== "LI" || e.parent === null || els[e.parent]?.role !== "L") continue;
    const item: PlanItem = { el: e.i, list: listOf(e.parent), sub: null };
    item.list.items.push(item);
    items.set(e.i, item);
  }
  for (const [i, l] of lists) {
    const body = els[i].parent === null ? undefined : els[els[i].parent!];
    const over = body?.role === "LBody" && body.parent !== null ? items.get(body.parent) : undefined;
    if (!over) continue;
    l.under = over;
    l.tail = body!.kids[body!.kids.length - 1] === i; // nothing of the item's own comes after the nested list
    if (l.tail) over.sub = l;
  }
  const bad = new Map<number, string>();
  ops.forEach((op, n) => {
    if (op.op === "make_list") {
      const into = els[op.element]?.role === "L" ? listOf(op.element) : { items: [], under: null, tail: true };
      for (const i of [op.element, ...op.absorb]) {
        if (els[i]?.role === "L") {
          if (i === op.element) continue;
          for (const it of listOf(i).items.splice(0)) { it.list = into; into.items.push(it); }
        } else if (els[i] && !items.has(i)) {
          const it: PlanItem = { el: i, list: into, sub: null };
          into.items.push(it);
          items.set(i, it);
        }
      }
      return;
    }
    if (op.op !== "indent" && op.op !== "outdent") return;
    const it = items.get(op.element);
    if (!it) { bad.set(n, "Only a list item can change level."); return; }
    const from = it.list, at = from.items.indexOf(it);
    if (op.op === "indent") {
      if (at === 0) { bad.set(n, "The first item of a list has no item before it to go under."); return; }
      const before = from.items[at - 1];
      const sub = (before.sub ??= { items: [], under: before, tail: true });
      from.items.splice(at, 1);
      sub.items.push(it);
      it.list = sub;
      return;
    }
    const over = from.under;
    if (!over) { bad.set(n, "This item is already at the top level of its list."); return; }
    if (!from.tail) { bad.set(n, "The item above has text after this nested list; move that text first."); return; }
    const rest = from.items.splice(at).slice(1);
    over.list.items.splice(over.list.items.indexOf(over) + 1, 0, it);
    it.list = over.list;
    if (rest.length) {
      const sub = (it.sub ??= { items: [], under: it, tail: true });
      for (const r of rest) { r.list = sub; sub.items.push(r); }
    }
    if (!from.items.length) over.sub = null;
  });
  const itemOf = (el: number): number | null => {
    for (let i: number | null = el, hops = 0; i !== null && els[i] && hops < 6; i = els[i].parent, hops++) {
      if (items.has(i)) return i;
      if (els[i].role === "L") return null; // a list isn't one of its items
    }
    return null;
  };
  return {
    bad, itemOf,
    level: (el) => {
      const it = items.get(el);
      if (!it) return null;
      let n = 1;
      for (let l = it.list; l.under; l = l.under.list) n++;
      return n;
    },
  };
}

/** Tags that pending edits turn into list items, join to another list, or build a list from. */
export function listMembers(ops: TagEdit[]): Set<number> {
  const out = new Set<number>();
  for (const op of ops) if (op.op === "make_list" || op.op === "build_list") for (const i of [op.element, ...(op.absorb ?? [])]) out.add(i);
  return out;
}

/** Pending edits without the given tags' own, keeping what is left workable: a tag taken back out of a new
 * list leaves the list to the others, and level changes that no longer have a list item to act on go too. */
export function withoutEdits(map: TagMap, ops: TagEdit[], ids: number[]): TagEdit[] {
  let next: TagEdit[] = [];
  for (const op of ops) {
    if (op.op === "make_list" && !ids.includes(op.element)) {
      const absorb = op.absorb.filter((i) => !ids.includes(i));
      // With no other tag left there is no list to make: nobody asked for a list of one
      if (absorb.length) next.push(absorb.length === op.absorb.length ? op : { ...op, absorb });
    } else if (op.op === "build_list" && op.absorb?.some((i) => ids.includes(i))) {
      continue; // its rows are cut from every tag it took: without one of them the rest don't stand
    } else if (!("element" in op) || !ids.includes(op.element)) next.push(op); // the document's own edits stay
  }
  for (let bad = listPlan(map, next).bad; bad.size; bad = listPlan(map, next).bad) {
    const first = Math.min(...bad.keys());
    next = next.filter((_, n) => n !== first);
  }
  return next;
}

/** Pending edits without one tag's moves (the undo button on a moved tag in the tree). */
export function undoMoves(ops: TagEdit[], element: number): TagEdit[] {
  return ops.filter((op) => !(op.op === "move" && op.element === element));
}

/** The tags to show for one page: those with content on it and the groups around them. A tag with no place on
 * any page (a link with only its annotation) is kept with its parent. */
export function tagsOnPage(map: TagMap, page: number): Set<number> {
  const out = new Set<number>();
  for (const e of map.elements) {
    if (e.boxes.some((b) => b.page === page)) out.add(e.i);
  }
  for (const e of map.elements) {
    if (!e.boxes.length && e.parent !== null && out.has(e.parent)) out.add(e.i);
  }
  return out;
}

/** A document this long opens one page at a time: drawing every page at once takes a while. */
export const LONG_DOCUMENT_PAGES = 50;

/** The pages a tag has content on, in order (a section's are its contents' pages). */
export function tagPages(e: TagElement): number[] {
  return [...new Set(e.boxes.map((b) => b.page))].sort((a, b) => a - b);
}

/** In one-page view, whether going to a tag from the tree should ask first: the tag spans several pages and
 * starts on another one, so the page would change under the person. Null when there's nothing to ask. */
export function jumpQuestion(e: TagElement, showing: number): { first: number; last: number; pages: number; here: boolean } | null {
  const pages = tagPages(e);
  if (pages.length < 2 || pages[0] === showing) return null;
  return { first: pages[0], last: pages[pages.length - 1], pages: pages.length, here: pages.includes(showing) };
}
