// Tags unsaved changes make, as tags (#692): a tag from an area, the AI's tags for an area, the new part of a split.
// Each edit that makes tags carries an id, and later edits name its tags as {made: id, group: n} wherever they would
// put a tag's number; the engine finds them as they are when that edit's turn comes. The tag manager shows them as
// ordinary tags, numbered after the saved ones (`expand`): they can be selected, moved, nested into, merged, deleted
// and edited like any other. Changes made on those numbers are stored with the names again (`store`).
import type { TagEdit, TagElement, TagMap, TagPart } from "../api";
import { splitPreview } from "./figure";
import { builtPreview, unite, type Box, type BuiltRow } from "./outline";
import { tablePreview, tableReplaced } from "./tablegrid";

/** A tag an earlier edit of the unsaved changes makes: that edit's id, and which of its tags (an area's new tags are
 * one edit making several; the others make one, group 0). */
export type MadeRef = { made: string; group: number };

export const isMade = (v: unknown): v is MadeRef =>
  typeof v === "object" && v !== null && typeof (v as MadeRef).made === "string" && typeof (v as MadeRef).group === "number";

/** A new tag as the tag manager shows it: its number, the place in the edits of the edit making it, which of that
 * edit's tags it is, and the edit's id. */
export type MadeTag = { i: number; op: number; group: number; id: string };

/** The tag map with the new tags in it, the edits with every tag they name as a number of that map, and the new tags.
 * base: how many tags the saved file has (a number from there on is a new tag). stored: the edits as kept. */
export type View = { map: TagMap; ops: TagEdit[]; made: MadeTag[]; base: number; stored: TagEdit[] };

/** Edits whose new tags are shown as tags: from areas and splits, a figure's parts, and lists and tables built (whose
 * tags are those their previews draw, in that order: the engine names them the same way). */
const SHOWN = new Set(["tag_artifacts", "regroup", "split", "take_pieces", "cut_area", "build_list", "build_table",
  "rebuild_table"]);
/** Edits that can carry an id (the engine's MAKERS). A list made of tags makes only the list: its items are the tags. */
const NAMED = new Set([...SHOWN, "make_list"]);
/** Tags the engine places the AI's new tags in (role_rules.GROUPING). */
const GROUPING = new Set(["Document", "Part", "Art", "Sect", "Div", "NonStruct", "Private", "Form", "Index"]);

/** How many tags an edit can make (as the engine counts them, refs.MAKERS). */
function count(op: TagEdit): number {
  const cells = (rows: unknown[][]) => rows.reduce((n, r) => n + r.length, 0);
  switch (op.op) {
    case "regroup": return op.groups.length;
    case "build_list": return 2 * op.rows.length;
    case "build_table": return 1 + op.rows.length + cells(op.rows);
    case "rebuild_table": return 3 + op.rows.length + cells(op.rows) + (op.out?.length ?? 0);
    default: return NAMED.has(op.op) ? 1 : 0;
  }
}
/** Tags that hold others rather than content of their own. */
const HOLDERS = new Set(["L", "Table", "TR"]);

type Ref = number | MadeRef;
type Fn = (v: Ref) => Ref;

/** A copy of op with every tag it names passed through fn (as the engine's refs.map_refs). */
export function mapRefs(op: TagEdit, fn: Fn): TagEdit {
  if (!("element" in op)) return op;
  const f = fn as (v: number) => number;
  const out = { ...op, element: f(op.element) } as TagEdit & Record<string, unknown>;
  const o = op as TagEdit & Record<string, unknown>;
  if ((op.op === "move" || op.op === "set_toc_target") && op.target !== null) out.target = f(op.target);
  if (op.op === "link_note" && op.note !== null) out.note = f(op.note);
  if (op.op === "rebuild_table" && op.caption !== null && op.caption !== undefined) out.caption = f(op.caption);
  if (Array.isArray(o.absorb)) out.absorb = (o.absorb as number[]).map(f);
  if (op.op === "set_headers") out.headers = op.headers.map(f);
  if (op.op === "build_list") out.rows = op.rows.map((r) => (r.el !== undefined ? { ...r, el: f(r.el) } : r));
  if (op.op === "build_table") {
    out.rows = op.rows.map((row) => row.map((c) => ({ ...c, parts: c.parts.map((p) => (p.el !== undefined ? { ...p, el: f(p.el) } : p)) })));
  }
  if (op.op === "rebuild_table") {
    const cell = (r: number | [number, number]) => (Array.isArray(r) ? [f(r[0]), r[1]] as [number, number] : f(r));
    out.rows = op.rows.map((row) => row.map((c) => ({ ...c, from: c.from.map(cell) })));
    if (op.out) out.out = op.out.map((p) => ({ ...p, from: p.from.map(cell) }));
    // A cell is cut in the file as it was: by number only
    if (op.cuts) out.cuts = Object.fromEntries(Object.entries(op.cuts).map(([k, v]) => [String(f(Number(k))), v]));
  }
  return out;
}

const refsOf = (op: TagEdit): Ref[] => {
  const out: Ref[] = [];
  mapRefs(op, (v) => { out.push(v); return v; });
  return out;
};

/** The edits with an id on each edit that makes tags and has none. */
export function withIds(ops: TagEdit[]): TagEdit[] {
  const used = new Set(ops.map((o) => (o as { id?: string }).id).filter(Boolean));
  let changed = false;
  const out = ops.map((op) => {
    if (!NAMED.has(op.op) || (op as { id?: string }).id) return op;
    let id: string;
    do id = `t${Math.random().toString(36).slice(2, 8)}`; while (used.has(id));
    used.add(id);
    changed = true;
    return { ...op, id } as TagEdit;
  });
  return changed ? out : ops;
}

const join = (texts: (string | undefined)[]) => texts.map((t) => (t ?? "").trim()).filter(Boolean).join(" ");

/** The tag map and edits as the tag manager works on them: each new tag an ordinary tag, numbered after the saved
 * ones, placed where the engine will put it. */
export function expand(map: TagMap, stored: TagEdit[]): View {
  const base = map.elements.length;
  if (!stored.some((op) => SHOWN.has(op.op))) return { map, ops: stored, made: [], base, stored };
  const els = map.elements.slice();
  const copied = new Set<number>();
  const edit = (i: number) => {
    if (!copied.has(i)) { els[i] = { ...els[i], kids: [...els[i].kids] }; copied.add(i); }
    return els[i];
  };
  const arts = new Map((map.artifacts ?? []).map((a) => [`${a.page}:${a.seq}`, a]));
  const pieces = new Map((map.pieces ?? []).map((p) => [`${p.page}:${p.mcid}`, p]));
  const numbers = new Map<string, number>();
  const made: MadeTag[] = [];
  // A new tag's place: before or after the tag it's placed by (under that tag's parent), or in it, as its last child
  const place = (i: number, by: number, position: "before" | "after" | "in"): number | null => {
    const at = els[by];
    if (!at) return null;
    const parent = position === "in" ? by : at.parent;
    if (parent === null) return null;
    const kids = edit(parent).kids;
    const k = kids.indexOf(by);
    if (position === "in" || k < 0) kids.push(i);
    else kids.splice(position === "before" ? k : k + 1, 0, i);
    return parent;
  };
  const add = (at: number, id: string, group: number, by: number, position: "before" | "after" | "in",
    tag: { role: string; alt?: string; text: string; boxes: Box[]; parts?: TagPart[] }) => {
    const i = els.length;
    const anchor = els[by];
    const order = (anchor?.order ?? 0) + (position === "before" ? -0.5 : 0.5) + group / 1000;
    const el: TagElement = { i, parent: null, kids: [], role: tag.role, raw_role: tag.role, boxes: tag.boxes, own: true,
      page: tag.boxes[0]?.page ?? anchor?.page, order, made: true, ...(tag.alt ? { alt: tag.alt } : {}),
      ...(tag.text ? { text: tag.text } : {}), ...(tag.parts ? { parts: tag.parts } : {}) };
    els.push(el);
    el.parent = place(i, by, position);
    numbers.set(`${id}:${group}`, i);
    made.push({ i, op: at, group, id });
    return i;
  };
  // A tag an edit takes the place of: out of the tree, with nothing drawn (the tags standing for it are)
  const hollow = (i: number) => {
    const e = els[i];
    if (!e) return;
    if (e.parent !== null && els[e.parent]) {
      const kids = edit(e.parent).kids;
      const k = kids.indexOf(i);
      if (k >= 0) kids.splice(k, 1);
    }
    els[i] = { ...els[i], boxes: [], own: false };  // its own kids kept: the table editor reads a table it rebuilds
    copied.add(i);
  };
  // A list or table built in place of a tag, from its preview's rows (a row inside the row before it that is less
  // deep): its top rows take the tag's place, in order
  const build = (at: number, id: string, by: number, rows: BuiltRow[], gone: number[], rebuilt = false) => {
    const anchor = els[by];
    if (!anchor) return;
    const parent = anchor.parent;
    const top = parent !== null && els[parent] ? edit(parent).kids : null;
    let pos = top ? top.indexOf(by) : -1;
    const open: { depth: number; i: number }[] = [];
    rows.forEach((r, group) => {
      while (open.length && open[open.length - 1].depth >= r.depth) open.pop();
      const up = open.length ? open[open.length - 1].i : parent;
      const i = els.length;
      els.push({ i, parent: up, kids: [], role: r.role, raw_role: r.role, boxes: r.boxes, own: !HOLDERS.has(r.role),
        page: r.boxes[0]?.page ?? anchor.page, order: (anchor.order ?? 0) + group / 1000, made: true,
        ...(r.text ? { text: r.text } : {}), ...(rebuilt && r.role === "Table" ? { of: by } : {}) });
      if (open.length) els[up!].kids.push(i);
      else if (top) top.splice(pos < 0 ? top.length : pos++, 0, i);
      open.push({ depth: r.depth, i });
      numbers.set(`${id}:${group}`, i);
      made.push({ i, op: at, group, id });
    });
    for (const g of new Set([by, ...gone])) hollow(g);
  };
  // Tags whose content earlier edits make artifacts (deleted with what's in them, or marked as artifacts): their
  // pieces aren't tagged when a later edit's turn comes, so they don't place an area's new tags
  const untagged = new Set<number>();
  const untag = (i: number) => {
    const stack = [i];
    while (stack.length) { const k = stack.pop()!; if (untagged.has(k)) continue; untagged.add(k); stack.push(...(els[k]?.kids ?? [])); }
  };
  const toNumber = (v: Ref): Ref => (isMade(v) ? numbers.get(`${v.made}:${v.group}`) ?? -1 : v);
  const ops = stored.map((raw, at) => {
    const op = mapRefs(raw, toNumber);
    if ((op.op === "delete" && op.children === "delete") || op.op === "set_decorative") untag(op.element);
    if (!SHOWN.has(op.op)) return op;
    const id = (op as { id?: string }).id ?? `#${at}`;
    if (op.op === "tag_artifacts") {
      const got = [...op.sections].sort((a, b) => a - b).map((s) => arts.get(`${op.page}:${s}`)).filter((a) => a !== undefined);
      add(at, id, 0, op.element, op.position, { role: op.role, alt: op.alt, text: join(got.map((a) => a.text)),
        boxes: unite(got.map((a) => ({ page: a.page, bbox: a.bbox }))),
        // Its content in the order the page draws it, as the engine marks it
        parts: got.map((a, k) => ({ k, kind: "content", page: a.page, bbox: a.bbox, text: a.text })) });
    } else if (op.op === "regroup") {
      // Where the engine puts them: before the tag at block level holding the first of their pieces, else by the anchor
      let by = op.element, position = op.position;
      const first = op.groups.flatMap((g) => g.pieces ?? []).map((m) => pieces.get(`${op.page}:${m}`)?.el)
        .find((x) => x !== undefined && !untagged.has(x));
      if (first !== undefined && els[first]) {
        let e = els[first];
        while (e.parent !== null && els[e.parent] && !GROUPING.has(els[e.parent].role)) e = els[e.parent];
        if (e.parent !== null) { by = e.i; position = "before"; }
      }
      let prev: number | null = null;
      op.groups.forEach((g, n) => {
        const ps = (g.pieces ?? []).map((m) => pieces.get(`${op.page}:${m}`)).filter((p) => p !== undefined);
        const as = (g.sections ?? []).map((s) => arts.get(`${op.page}:${s}`)).filter((a) => a !== undefined);
        const tag = { role: g.role, alt: g.alt, text: join([...ps.map((p) => p.text), ...as.map((a) => a.text)]),
          boxes: unite([...ps, ...as].map((x) => ({ page: x.page, bbox: x.bbox })).concat(g.box ? [{ page: op.page, bbox: g.box }] : [])) };
        prev = prev === null ? add(at, id, n, by, position, tag) : add(at, id, n, prev, "after", tag);
      });
    } else if (op.op === "take_pieces" || op.op === "cut_area") {
      // A figure's part: right after the figure
      const part = splitPreview({ ...map, elements: els }, [op]).get(op.element)?.[0];
      if (part) add(at, id, 0, op.element, "after", { role: part.role, alt: part.alt, text: "", boxes: part.boxes });
    } else if (op.op === "build_list") {
      const view = { ...map, elements: els };
      build(at, id, op.element, builtPreview(view, op), op.absorb ?? []);
    } else if (op.op === "build_table" || op.op === "rebuild_table") {
      const view = { ...map, elements: els };
      build(at, id, op.element, tablePreview(view, op), tableReplaced(view, op), op.op === "rebuild_table");
    } else if (op.op === "split") {
      // The /K items from `at` on (split inside that item's text, `char`, from that character); the tag keeps the rest
      const e = els[op.element];
      if (!e) return op;
      const parts = e.parts ?? [];
      const moved = parts.filter((p) => p.k >= op.at);
      const texts = moved.map((p) => (p.k === op.at && op.char ? (p.text ?? "").slice(op.char) : p.text));
      // A tag whose text the tag map keeps on the tag, not its pieces (one piece, or no parts listed): cut the tag's
      // own text there, and draw it in the tag's box
      const whole = parts.length <= 1 && !parts[0]?.text ? (e.say ?? e.text ?? "") : "";
      const text = join(texts) || (whole && op.at === 0 ? whole.slice(op.char ?? 0).trim() : "");
      const boxes = unite(moved.filter((p) => p.page && p.bbox).map((p) => ({ page: p.page!, bbox: p.bbox! })));
      add(at, id, 0, op.element, "after", { role: op.role ?? e.role, text,
        boxes: boxes.length || parts.length ? boxes : e.boxes.map((b) => ({ page: b.page, bbox: b.bbox })),
        parts: moved.map((p) => ({ ...p, k: p.k - op.at, ...(p.k === op.at && op.char && p.text ? { text: p.text.slice(op.char) } : {}) })) });
      if (parts.length) {
        edit(op.element).parts = parts.filter((p) => p.k < op.at || (p.k === op.at && !!op.char))
          .map((p) => (p.k === op.at && op.char && p.text ? { ...p, text: p.text.slice(0, op.char) } : p));
      }
    }
    return op;
  });
  return { map: { ...map, elements: els }, ops, made, base, stored };
}

const DANGLING: MadeRef = { made: "", group: -1 };

/** The edits without those naming a tag no earlier edit makes (its edit taken out, or the tag taken back), each of
 * those taken out in turn, and how many went. */
export function prune(ops: TagEdit[]): { ops: TagEdit[]; dropped: number } {
  const have = new Map<string, number>();
  const out: TagEdit[] = [];
  for (const op of ops) {
    const ok = refsOf(op).every((v) => !isMade(v) || (v !== DANGLING && v.group < (have.get(v.made) ?? 0)));
    if (!ok) continue;
    out.push(op);
    const id = (op as { id?: string }).id;
    if (id && NAMED.has(op.op)) have.set(id, count(op));
  }
  return { ops: out.length === ops.length ? ops : out, dropped: ops.length - out.length };
}

/** Edits made on the view's numbers, kept with the new tags' names. An edit left as it was is kept as it was stored.
 * Edits that name a tag no longer made go too (`dropped` says how many). */
export function store(view: View, next: TagEdit[]): { ops: TagEdit[]; dropped: number } {
  const was = new Map(view.ops.map((op, n) => [op, view.stored[n]]));
  const toRef = (v: Ref): Ref => {
    if (typeof v !== "number" || v < view.base) return v;
    const m = view.made[v - view.base];
    return m ? { made: m.id, group: m.group } : DANGLING;
  };
  return prune(withIds(next.map((op) => was.get(op) ?? mapRefs(op, toRef))));
}

/** The edits with a new tag taken back: one of an area's new tags leaves the edit (the others' names follow), the
 * last of them or any other new tag takes the edit with it. The changes made to it go too. */
export function takeBack(stored: TagEdit[], at: number, group = 0): { ops: TagEdit[]; dropped: number } {
  const op = stored[at];
  if (!op) return { ops: stored, dropped: 0 };
  const id = (op as { id?: string }).id;
  if (op.op === "regroup" && op.groups.length > 1) {
    const renumber = (v: Ref): Ref => (!isMade(v) || v.made !== id ? v : v.group === group ? DANGLING
      : v.group > group ? { made: v.made, group: v.group - 1 } : v);
    return prune(stored.map((o, n) => (n === at ? { ...op, groups: op.groups.filter((_, k) => k !== group) }
      : n > at && id ? mapRefs(o, renumber) : o)));
  }
  return prune(stored.filter((_, n) => n !== at));
}
