/** The tag editor's menu: what can be done with the selected tag or tags, each as the edits it would add. Kept
 * apart from the menu's drawing so the choices can be tested: which are offered, which are refused and why.
 *
 * Everything here is an edit the engine knows (web/src/api.ts, TagEdit). */
import type { TagEdit, TagElement, TagMap } from "../api";
import { ROLE_NAMES, absorbedBy, addEdit, listMembers, listPlan, overrides, pageOrder, withoutEdits } from "./draft";
import { actionNote, checkElementRole, checkMove } from "./roles";
import { outlineLines } from "./outline";

export type MenuAct = "split-text" | "delete" | "read" | "report" | "show" | "types" | "area-draw" | "area-page" | "area-clear" | "build" | "isolate" | "unisolate" | "edit" | "figure-split";

export type MenuItem = {
  id: string;
  label: string;
  group: "type" | "shape" | "list" | "order" | "note" | "area" | "more";
  note?: string;       // what else choosing it does ("the text is wrapped in a list with one item")
  disabled?: string;   // why it can't be chosen now
  ops?: TagEdit[];     // the unsaved changes as they would be after choosing it
  act?: MenuAct;       // or something the viewer does that isn't an edit
};

/** The types offered from the menu: the ones people change between. The panel has the full list. */
export const MENU_ROLES = ["P", "H1", "H2", "H3", "H4", "H5", "H6", "L", "Caption", "Figure", "Formula"];

/** Text tags that can become a list item as they are (the engine's LIST_ITEM_FROM). */
export const LIST_ITEM_FROM = ["H", "H1", "H2", "H3", "H4", "H5", "H6", "P", "Caption", "BlockQuote", "Note", "Code", "BibEntry"];
const BLOCKS = ["H", "H1", "H2", "H3", "H4", "H5", "H6", "P", "L", "Table", "Sect", "Div", "Art", "Part", "BlockQuote", "TOC", "Index", "Document"];

/** Where on a page the menu was asked for, in page units (to split a tag at that place). */
export type MenuPoint = { page: number; x: number; y: number };

const short = (e: TagElement) => `${ROLE_NAMES[e.role] ?? e.role}${e.text ? ` “${e.text.slice(0, 24)}${e.text.length > 24 ? "…" : ""}”` : ""}`;

function isInside(map: TagMap, inner: number, outer: number): boolean {
  for (let p = map.elements[inner]?.parent ?? null; p !== null && p !== undefined; p = map.elements[p]?.parent ?? null) {
    if (p === outer) return true;
  }
  return false;
}

/** The list a table of contents would be made from for this tag: the tag itself or the list it's in, going out to the
 * outermost one, since a nested list comes along with the list around it. Null when the tag isn't in a list. */
function outermostList(map: TagMap, e: TagElement): TagElement | null {
  let found: TagElement | null = null;
  for (let c: TagElement | undefined = e; c; c = c.parent === null ? undefined : map.elements[c.parent]) {
    if (c.role === "L") found = c;
    else if (!["LI", "LBody", "Lbl"].includes(c.role) && found) break;
    else if (!found && !["LI", "LBody", "Lbl", "Span", "Link", "Reference", "Quote"].includes(c.role) && c !== e) return null;
  }
  return found;
}

/** The note a tag points at, with a pending change applied, or null. */
export function pointsAt(map: TagMap, ops: TagEdit[], i: number): number | null {
  let out: number | null = map.elements[i]?.refs?.find((r) => map.elements[r]?.role === "Note") ?? null;
  for (const op of ops) if (op.op === "link_note" && op.element === i) out = op.note;
  return out;
}

/** The selected tags in the order they're read (tags with no place in it last, in tree order). */
export function inReadingOrder(map: TagMap, ids: number[]): TagElement[] {
  const seen = new Set<number>();
  return ids.filter((i) => map.elements[i] && !seen.has(i) && seen.add(i)).map((i) => map.elements[i])
    .sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity) || a.i - b.i);
}

export function tagMenu(map: TagMap, ops: TagEdit[], ids: number[], at: MenuPoint | null = null, canEdit = true): MenuItem[] {
  const els = inReadingOrder(map, ids);
  if (!els.length) return [];
  const one = els.length === 1 ? els[0] : null;
  const many = els.length > 1;
  const items: MenuItem[] = [];
  const readOnly = canEdit ? undefined : "This version can't be edited.";

  // --- change type ---
  for (const role of MENU_ROLES) {
    if (els.every((e) => e.role === role)) continue;
    const verdicts = els.map((e) => (e.role === role ? null : checkElementRole(map, e, role)));
    const refused = verdicts.find((v) => v && !v.ok);
    const notes = [...new Set(verdicts.map((v) => (v && v.ok ? actionNote(v, role) : null)).filter(Boolean))] as string[];
    items.push({
      id: `role:${role}`, group: "type", label: ROLE_NAMES[role] ?? role,
      // Several tags each change on their own: a list made here is one list per tag
      note: many && role === "L" ? "Each tag becomes its own list with one item." : notes[0],
      disabled: readOnly ?? (refused ? refused.reason : undefined),
      ops: refused ? undefined : els.reduce((acc, e) => (e.role === role ? acc
        : addEdit(acc, { op: "set_role", element: e.i, expect_role: e.role, role })), ops),
    });
  }

  // --- merge and split ---
  const absorbed = absorbedBy(ops);
  if (many) {
    const nested = els.some((a) => els.some((b) => a.i !== b.i && isInside(map, a.i, b.i)));
    const why = readOnly ?? (els.some((e) => !e.own) ? "A tag that only groups other tags can't be merged; select the tags inside it."
      : nested ? "A tag can't be merged with a tag that contains it."
      : els.some((e) => absorbed.has(e.i)) ? "One of these is already being merged into another tag." : undefined);
    const [into, ...rest] = els;
    const existing = ops.findIndex((o) => o.op === "merge" && o.element === into.i);
    let next = ops;
    if (!why) {
      if (existing >= 0) {
        const op = ops[existing] as Extract<TagEdit, { op: "merge" }>;
        next = ops.slice();
        next[existing] = { ...op, absorb: [...op.absorb, ...rest.map((e) => e.i).filter((i) => !op.absorb.includes(i))] };
      } else {
        next = [...ops, { op: "merge", element: into.i, expect_role: into.role, absorb: rest.map((e) => e.i) }];
      }
    }
    items.push({ id: "merge", group: "shape", label: `Merge the ${els.length} selected tags into one`,
      note: `They become one ${ROLE_NAMES[into.role] ?? into.role}, in reading order.`, disabled: why, ops: why ? undefined : next });
  }
  // --- lists ---
  const members = listMembers(ops);
  if (many) {
    const nested = els.some((a) => els.some((b) => a.i !== b.i && isInside(map, a.i, b.i)));
    const wrong = els.find((e) => e.role !== "L" && !LIST_ITEM_FROM.includes(e.role));
    const blocks = els.find((e) => e.role !== "L" && e.kids.some((k) => BLOCKS.includes(map.elements[k]?.role)));
    const why = readOnly ?? (wrong ? `A ${ROLE_NAMES[wrong.role] ?? wrong.role} can't become a list item. Select paragraphs, headings or lists.`
      : blocks ? `${short(blocks)} holds other blocks, so it can't become a list item.`
      : nested ? "A tag can't join a list with a tag that contains it."
      : els.some((e) => absorbed.has(e.i)) ? "One of these is being merged into another tag."
      : els.some((e) => members.has(e.i)) ? "One of these is already becoming part of a list. Undo that first, or save."
      : undefined);
    const [first, ...rest] = els;
    const lists = els.filter((e) => e.role === "L").length;
    items.push({ id: "make-list", group: "list",
      label: lists === els.length ? `Join the ${els.length} selected lists into one` : `Make one list from the ${els.length} selected tags`,
      note: lists === els.length ? "Their items follow each other, in reading order."
        : `Each tag becomes one item, in reading order${lists ? "; a list among them gives its items" : ""}.`,
      disabled: why, ops: why ? undefined : [...ops, { op: "make_list", element: first.i, expect_role: first.role, absorb: rest.map((e) => e.i) }] });
  }
  // Building a list out of the lines of a paragraph (or of several): offered when there are lines to work with
  if (els.every((e) => LIST_ITEM_FROM.includes(e.role) && e.own) && outlineLines(els).length >= 2) {
    const spoken = els.find((e) => e.actual_text || e.alt);
    const blocks = els.find((e) => e.kids.some((k) => BLOCKS.includes(map.elements[k]?.role)));
    const busy = els.some((e) => absorbed.has(e.i) || members.has(e.i) || ops.some((o) => o.element === e.i && (o.op === "split" || o.op === "merge" || o.op === "set_role")));
    items.push({ id: "build", group: "list", label: "Build a list from the lines…", act: "build",
      note: "Set each line's level in a window, then apply it as one change.",
      disabled: readOnly ?? (spoken ? `${short(spoken)} has ${spoken.actual_text ? "read-aloud text" : "alt text"} that stands for all of it. Remove that first.`
        : blocks ? `${short(blocks)} holds other blocks.`
        : els.length > 1 && els.some((a) => els.some((b) => a.i !== b.i && isInside(map, a.i, b.i))) ? "A tag can't be built into a list with a tag that contains it."
        : busy ? "One of these has an unsaved change to its type or shape. Save or undo that first." : undefined) });
  }
  {
    // Levels: for list items, tags that are becoming items, and anything selected inside an item (its text)
    const plan = listPlan(map, ops);
    const targets = [...new Set(els.map((e) => plan.itemOf(e.i)))];
    if (targets.length && targets.every((t) => t !== null)) {
      const level = (kind: "indent" | "outdent", id: string, label: string, note: string) => {
        const next = [...ops, ...(targets as number[]).map((t): TagEdit => ({ op: kind, element: t, expect_role: map.elements[t].role }))];
        const bad = listPlan(map, next).bad;
        const toc = (targets as number[]).some((t) => ops.some((o) => o.op === "make_toc" && isInside(map, t, o.element)));
        const why = readOnly ?? (toc ? "It's becoming a table of contents. Change its levels before that, or after saving it as a list."
          : bad.size ? bad.values().next().value : undefined);
        items.push({ id, group: "list", label, note, disabled: why, ops: why ? undefined : next });
      };
      const s = targets.length > 1 ? "s" : "";
      level("indent", "indent", `Indent the item${s}`, `One level in, under the item before ${s ? "them" : "it"}.`);
      level("outdent", "outdent", `Outdent the item${s}`, `One level out. Items after ${s ? "them" : "it"} in the nested list stay under ${s ? "them" : "it"}.`);
    }
  }
  // A list (or a tag inside one) can become a table of contents: the outermost list, with its nested lists
  const top = one ? outermostList(map, one) : null;
  if (top) {
    const inside = (i: number) => i === top.i || isInside(map, i, top.i);
    // The engine keeps a paragraph or nested list in an item's body; another block has no place in an entry
    const odd = map.elements.filter((e) => e.role === "LBody" && inside(e.i)).flatMap((e) => e.kids.map((k) => map.elements[k]))
      .find((k) => k && BLOCKS.includes(k.role) && k.role !== "P" && k.role !== "L");
    const busy = ops.some((o) => o.op !== "make_toc" && [o.element, ...("absorb" in o ? o.absorb ?? [] : [])].some(inside)
      && ["set_role", "make_list", "build_list", "merge", "split", "delete", "move"].includes(o.op));
    const pending = ops.some((o) => o.op === "make_toc" && o.element === top.i);
    const why = readOnly ?? (odd ? `An item holds ${short(odd)}; an entry can hold only its label, text, paragraphs and nested entries.`
      : busy ? "The list has unsaved changes to its shape or types. Save or undo those first." : undefined);
    items.push(pending
      ? { id: "toc", group: "list", label: "Keep it a list", note: "Undo making it a table of contents.", disabled: readOnly,
        ops: ops.filter((o) => !(o.op === "make_toc" && o.element === top.i)) }
      : { id: "toc", group: "list", label: top.i === one!.i ? "Make it a table of contents" : "Make the list a table of contents",
        note: "Each item becomes an entry, its text a reference; nested lists come along.",
        disabled: why, ops: why ? undefined : [...ops, { op: "make_toc", element: top.i, expect_role: top.role }] });
  }
  if (one) {
    const already = ops.some((o) => o.op === "split" && o.element === one.i);
    const parts = one.parts ?? [];
    const hit = at ? parts.findIndex((p) => p.kind !== "link" && p.page === at.page && p.bbox
      && at.x >= p.bbox[0] - 1 && at.x <= p.bbox[2] + 1 && at.y >= p.bbox[1] - 1 && at.y <= p.bbox[3] + 1) : -1;
    if (parts.length >= 2 && hit > 0) {
      items.push({ id: "split-here", group: "shape", label: "Split here",
        note: "Everything from this point on becomes a new tag of the same type.",
        disabled: readOnly ?? (already ? "This tag is already being split." : undefined),
        ops: already ? undefined : [...ops, { op: "split", element: one.i, expect_role: one.role, at: parts[hit].k }] });
    }
    if (one.own && (one.text || parts.length >= 2)) {
      items.push({ id: "split-text", group: "shape", label: hit > 0 ? "Split somewhere else…" : "Split…",
        note: "Click where the new tag should start, in a window.", disabled: readOnly ?? (already ? "This tag is already being split." : undefined),
        act: "split-text" });
    }
    // A figure of several pieces is split by picking them; one drawn as a single piece, by cutting a box out of it
    if ((one.role === "Figure" || one.role === "Formula") && (map.pieces ?? []).some((p) => p.el === one.i)) {
      items.push({ id: "figure-split", group: "shape", label: `Split the ${one.role === "Figure" ? "figure" : "formula"}…`, act: "figure-split",
        note: "Choose which drawings and labels become a figure of their own.",
        disabled: readOnly ?? (ops.some((o) => o.op === "take_pieces" && o.element === one.i) ? "It's already being split. Save first, or undo that." : undefined) });
    }
    if (one.role === "Figure") {
      items.push({ id: "decorative", group: "shape", label: "Mark as decorative", note: "Screen readers skip it.", disabled: readOnly,
        ops: addEdit(ops, { op: "set_decorative", element: one.i, expect_role: one.role }) });
    }

    // --- reading order ---
    const page = one.page ?? one.boxes[0]?.page;
    const list = page ? pageOrder(map, page, ops) : [];
    const idx = list.findIndex((e) => e.i === one.i);
    const roleOf = (i: number | null | undefined) => (i === null || i === undefined ? null : map.elements[i]?.role ?? null);
    const move = (target: TagElement | undefined, position: "before" | "after", id: string, label: string) => {
      if (idx < 0) return;
      const v = target ? checkMove(one.role, roleOf(one.parent), roleOf(target.parent), one.parent === target.parent) : null;
      items.push({ id, group: "order", label, note: target ? `${position === "before" ? "Before" : "After"} ${short(target)}.` : undefined,
        disabled: readOnly ?? (!target ? (position === "before" ? "It's already first on its page." : "It's already last on its page.")
          : v && !v.ok ? v.reason : undefined),
        ops: target && v?.ok ? [...ops, { op: "move", element: one.i, expect_role: one.role, target: target.i,
          expect_target_role: target.role, position }] : undefined });
    };
    move(list[idx - 1], "before", "earlier", "Read it earlier");
    move(list[idx + 1], "after", "later", "Read it later");
    // A footnote the rules didn't link: the mark (or the text it's in) points at its note
    const over = overrides(ops, map);
    const roleOf2 = (e: TagElement) => over.get(e.i)?.role ?? e.role;
    if (roleOf2(one) !== "Note") {
      const near = (e: TagElement) => Math.abs((e.page ?? 0) - (one.page ?? 0));
      const notes = map.elements.filter((e) => roleOf2(e) === "Note" && e.i !== one.i && near(e) <= 1)
        .sort((a, b) => near(a) - near(b) || a.i - b.i).slice(0, 12);
      const now = pointsAt(map, ops, one.i);
      for (const n of notes) {
        items.push({ id: `note:${n.i}`, group: "note", label: `Points at “${(n.text ?? "a note").slice(0, 40)}”${n.page !== one.page ? ` (page ${n.page})` : ""}`,
          disabled: readOnly ?? (now === n.i ? "It already points at this note." : undefined),
          ops: addEdit(ops, { op: "link_note", element: one.i, expect_role: one.role, note: n.i }) });
      }
      if (now !== null) {
        items.push({ id: "note:none", group: "note", label: "Doesn't point at a footnote", disabled: readOnly,
          ops: addEdit(ops, { op: "link_note", element: one.i, expect_role: one.role, note: null }) });
      }
    }
    items.push({ id: "edit", group: "more", label: canEdit ? "Edit tag…" : "Tag details…", act: "edit",
      note: "What it says, its findings, alt text, read-aloud text and language." });
    items.push({ id: "delete", group: "more", label: "Remove or delete this tag…", disabled: readOnly ?? (one.parent === null
      ? "The document's top tag stays." : undefined), act: "delete" });
  }

  // --- the rest ---
  const mine = ops.filter((o) => ids.includes(o.element) || ((o.op === "make_list" || o.op === "build_list") && o.absorb?.some((i) => ids.includes(i))));
  if (mine.length) {
    items.push({ id: "undo", group: "more", label: many ? "Undo these tags' unsaved changes" : "Undo this tag's unsaved changes",
      ops: withoutEdits(map, ops, ids) });
  }
  if (one) {
    items.push({ id: "read", group: "more", label: "Read aloud from here", act: "read" });
    items.push({ id: "report", group: "more", label: "Report a problem with this tag", act: "report" });
  }
  return items;
}

/** The menu for a right-click on a page where there is no tag: the panel's area tools, for content that has no
 * tag of its own to click (text the tagger left out, or a piece sitting in the wrong tag). */
export function pageMenu(page: number, hasArea: boolean, drawing: boolean, canEdit = true): MenuItem[] {
  const readOnly = canEdit ? undefined : "This version can't be edited.";
  const items: MenuItem[] = [
    { id: "area-draw", group: "area", label: drawing ? "Stop choosing an area" : "Draw an area on the page", disabled: readOnly,
      note: drawing ? undefined : "Drag a box over the content. The panel then lists what's inside it, to tag or to move.", act: "area-draw" },
    { id: "area-page", group: "area", label: `Use all of page ${page}`, note: "The panel lists everything on the page.", disabled: readOnly, act: "area-page" },
  ];
  if (hasArea) items.push({ id: "area-clear", group: "area", label: "Clear the area", act: "area-clear" });
  return items;
}

/** The menu as it is shown: the types go to a dialog of their own, opened from one line of the menu, since a
 * dozen types with what each does made the menu taller than the window. Returns that menu and the types. */
export function menuAndTypes(items: MenuItem[]): { menu: MenuItem[]; types: MenuItem[] } {
  const types = items.filter((i) => i.group === "type");
  const rest = items.filter((i) => i.group !== "type");
  if (!types.length) return { menu: rest, types };
  // When no type can be chosen and they all say the same thing (a version that can't be edited), say it here
  const reasons = new Set(types.map((t) => t.disabled));
  const why = reasons.size === 1 ? [...reasons][0] : undefined;
  return { menu: [{ id: "types", group: "type", label: "Change type…", disabled: why, act: "types" }, ...rest], types };
}

/** The menu's top level: the main kinds of change each open a submenu of their own, so the menu stays short; a
 * kind with a single choice ("Change type…") and the everyday actions under "More" are on the top level itself. */
export type MenuEntry = { kind: "item"; item: MenuItem } | { kind: "group"; group: MenuItem["group"]; label: string; items: MenuItem[]; none: boolean };

export const GROUP_NAMES: Record<MenuItem["group"], string> = { type: "Type", shape: "Merge and split", list: "Lists", order: "Reading order", note: "Footnote", area: "Areas", more: "More" };
const GROUP_ORDER: MenuItem["group"][] = ["type", "shape", "list", "order", "note", "area", "more"];
const FLAT: MenuItem["group"][] = ["area", "more"];

export function menuEntries(items: MenuItem[]): MenuEntry[][] {
  const sections: MenuEntry[][] = [];
  let submenus: MenuEntry[] = [];
  for (const group of GROUP_ORDER) {
    const mine = items.filter((i) => i.group === group);
    if (!mine.length) continue;
    if (FLAT.includes(group)) {
      if (submenus.length) { sections.push(submenus); submenus = []; }
      sections.push(mine.map((item) => ({ kind: "item", item })));
    } else if (mine.length === 1) {
      submenus.push({ kind: "item", item: mine[0] });
    } else {
      // "none": nothing in it can be chosen now; it still opens, to say why
      submenus.push({ kind: "group", group, label: GROUP_NAMES[group], items: mine, none: mine.every((i) => i.disabled) });
    }
  }
  if (submenus.length) sections.unshift(submenus);
  return sections;
}

/** A plain click selects one tag; with Ctrl or Cmd it adds or takes away; with Shift it takes everything from the
 * last tag chosen to this one, in the order `visible` lists them. Returns the selection, the tag acted on first. */
export function pick(current: number[], id: number, how: "one" | "toggle" | "range", visible: number[] = []): number[] {
  if (how === "one" || current.length === 0) return [id];
  if (how === "toggle") {
    if (!current.includes(id)) return [...current, id];
    return current.length > 1 ? current.filter((i) => i !== id) : current;
  }
  const from = visible.indexOf(current[0]), to = visible.indexOf(id);
  if (from < 0 || to < 0) return [...new Set([...current, id])];
  const span = from <= to ? visible.slice(from, to + 1) : visible.slice(to, from + 1).reverse();
  return [current[0], ...span.filter((i) => i !== current[0])];
}
