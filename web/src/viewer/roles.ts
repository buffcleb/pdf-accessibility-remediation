// Which tag types an element can be changed to. A copy of the engine's rules (app/tagging/role_rules.py):
// keep the two in step. The engine has the final say and refuses anything these let through by mistake.
import type { TagElement, TagMap } from "../api";

const HEADINGS = ["H", "H1", "H2", "H3", "H4", "H5", "H6"];
const TEXT = new Set([...HEADINGS, "P", "Caption", "BlockQuote", "Note", "Code", "Quote", "Span", "Reference", "BibEntry", "Figure", "Formula"]);
const BLOCKS = new Set([...HEADINGS, "P", "L", "Table", "Sect", "Div", "Art", "Part", "BlockQuote", "TOC", "Index", "Document"]);
const ALLOWED_KIDS: Record<string, Set<string>> = {
  Table: new Set(["TR", "THead", "TBody", "TFoot", "Caption"]),
  THead: new Set(["TR"]), TBody: new Set(["TR"]), TFoot: new Set(["TR"]),
  TR: new Set(["TH", "TD"]),
  L: new Set(["L", "LI", "Caption"]),
  LI: new Set(["Lbl", "LBody"]),
  TOC: new Set(["TOC", "TOCI", "Caption"]),
};
const PARENT_ONLY: Record<string, Set<string>> = {
  LI: new Set(["L"]), Lbl: new Set(["LI", "TOCI"]), LBody: new Set(["LI"]), TR: new Set(["Table", "THead", "TBody", "TFoot"]),
  TH: new Set(["TR"]), TD: new Set(["TR"]), THead: new Set(["Table"]), TBody: new Set(["Table"]), TFoot: new Set(["Table"]),
  TOCI: new Set(["TOC"]),
};
const NAMES: Record<string, string> = {
  L: "list", LI: "list item", TR: "table row", Table: "table", TOC: "table of contents", TOCI: "table of contents entry", THead: "table header",
  TBody: "table body", TFoot: "table footer", P: "paragraph",
};
const name = (r: string) => NAMES[r] ?? r;

export type Verdict = { ok: boolean; action: "none" | "collapse_list" | "wrap_list" | "wrap_lbody"; reason: string };

export function checkRole(next: string, role: string, parent: string | null, kidRoles: string[], hasContent: boolean): Verdict {
  const ok = (action: Verdict["action"] = "none"): Verdict => ({ ok: true, action, reason: "" });
  const no = (reason: string): Verdict => ({ ok: false, action: "none", reason });
  if (next === role) return ok();
  const kids = new Set(kidRoles);
  if (parent && ALLOWED_KIDS[parent] && !ALLOWED_KIDS[parent].has(next)) {
    return no(`Inside a ${name(parent)} a tag can only be ${[...ALLOWED_KIDS[parent]].sort().join(", ")}.`);
  }
  if (PARENT_ONLY[next] && !(parent && PARENT_ONLY[next].has(parent))) {
    return no(`A ${name(next)} has to be inside a ${[...PARENT_ONLY[next]].sort().map(name).join(" or ")}.`);
  }
  if (next === "TH" || next === "TD") return no("Use the header/data cell choice to change a table cell.");
  if (role === "L" && TEXT.has(next) && !["Span", "Quote", "Reference", "Figure", "Formula"].includes(next)) return ok("collapse_list");
  for (const k of [...kids].sort()) {
    if (PARENT_ONLY[k] && !PARENT_ONLY[k].has(next)) {
      return no(`This tag holds ${name(k)} tags, so it has to stay a ${[...PARENT_ONLY[k]].sort().map(name).join(" or ")}.`);
    }
  }
  if (ALLOWED_KIDS[next]) {
    if ([...kids].every((k) => ALLOWED_KIDS[next].has(k)) && !hasContent) return ok();
    if (![...kids].some((k) => BLOCKS.has(k))) {
      if (next === "L") return ok("wrap_list");
      if (next === "LI") return ok("wrap_lbody");
    }
    if (["Table", "TR", "THead", "TBody", "TFoot", "TOC"].includes(next)) {
      return no(`A ${name(next)} needs its own structure (${[...ALLOWED_KIDS[next]].sort().join(", ")}); it can't be made by changing one tag.`);
    }
    return no(`This tag holds other blocks, so it can't become a ${name(next)}.`);
  }
  const blocks = [...kids].filter((k) => BLOCKS.has(k)).sort();
  if (TEXT.has(next) && blocks.length) {
    return no(`This tag holds other blocks (${blocks.join(", ")}); a ${name(next)} can't. Change the tags inside it instead.`);
  }
  return ok();
}

/** The check for one element of a tag map (roles as they are now, before pending changes). */
export function checkElementRole(map: TagMap, el: TagElement, next: string): Verdict {
  const parent = el.parent !== null ? map.elements[el.parent]?.role ?? null : null;
  return checkRole(next, el.role, parent, el.kids.map((k) => map.elements[k]?.role ?? ""), el.own);
}

export function actionNote(v: Verdict, next: string): string | null {
  if (v.action === "collapse_list") return `The list is taken apart: each list item becomes its own ${next} tag, and lists nested in an item follow it.`;
  if (v.action === "wrap_list") return "The text is wrapped in a list with one item (L > LI > LBody).";
  if (v.action === "wrap_lbody") return "The text is wrapped in the item's body (LI > LBody).";
  return null;
}

// Tags that may only move among their own siblings (a copy of the engine's STAY_IN_PARENT)
const STAY_IN_PARENT = new Set(["TH", "TD", "Lbl", "LBody"]);

/** Can a tag `role` move from under `oldParent` to under `newParent`? A copy of the engine's check_move. */
export function checkMove(role: string, oldParent: string | null, newParent: string | null, sameParent: boolean): Verdict {
  const ok: Verdict = { ok: true, action: "none", reason: "" };
  const no = (reason: string): Verdict => ({ ok: false, action: "none", reason });
  if (oldParent === null) return no("The document's top tag can't be moved.");
  if (newParent === null) return no("Tags can't be placed outside the document's top tag.");
  if (STAY_IN_PARENT.has(role) && !sameParent) {
    return no(role === "TH" || role === "TD"
      ? "A table cell can only move within its own row: in another row it would make the rows uneven."
      : `A list item's ${role === "Lbl" ? "label" : "body"} can only move within its own item.`);
  }
  if (ALLOWED_KIDS[newParent] && !ALLOWED_KIDS[newParent].has(role)) {
    return no(`Inside a ${name(newParent)} a tag can only be ${[...ALLOWED_KIDS[newParent]].sort().join(", ")}.`);
  }
  if (PARENT_ONLY[role] && !PARENT_ONLY[role].has(newParent)) {
    return no(`A ${name(role)} has to be inside a ${[...PARENT_ONLY[role]].sort().map(name).join(" or ")}.`);
  }
  if (TEXT.has(newParent) && newParent !== "Figure" && newParent !== "Formula" && BLOCKS.has(role)) {
    return no(`A ${name(role)} can't go inside a ${name(newParent)}.`);
  }
  return ok;
}

// Tags that hold content of their own, so content can be moved into them (the engine's CONTENT_ROLES)
export const CONTENT_ROLES = new Set([...HEADINGS, "P", "Caption", "BlockQuote", "Note", "Code", "Quote", "Span", "Reference",
  "BibEntry", "Figure", "Formula", "TD", "TH", "LBody", "Lbl", "Link", "Form", "TOCI"]);
// Types a new tag made from untagged items can have (the engine's NEW_AREA_ROLES)
export const NEW_AREA_ROLES = ["P", "H1", "H2", "H3", "H4", "H5", "H6", "Figure", "Formula", "Caption", "Note", "Quote", "BlockQuote", "Code"];
