// What "Read aloud" says: the tags in reading order, worded like a (much simplified) screen reader. Pure functions,
// so the spoken sequence can be tested without a browser.
import type { TagEdit, TagElement, TagMap } from "../api";
import { absorbedBy, draftTree, overrides, pageOrder } from "./draft";

export type Utterance = {
  el: number; // the tag to show while this is read
  page: number;
  text: string;
  lang?: string;
};

const HEADING = /^H([1-6])$/;

/** The words for each tag, in reading order through the whole document, with pending edits applied. */
export function speechScript(map: TagMap, ops: TagEdit[] = []): Utterance[] {
  const over = overrides(ops, map);
  const gone = absorbedBy(ops);
  const tree = draftTree(map, ops);
  const roleOf = (i: number) => over.get(i)?.role ?? map.elements[i]?.role ?? "";
  const altOf = (i: number) => (over.has(i) && over.get(i)!.alt !== undefined ? over.get(i)!.alt : map.elements[i]?.alt);
  const actualOf = (i: number) => (over.has(i) && over.get(i)!.actual !== undefined ? over.get(i)!.actual : map.elements[i]?.actual_text);
  const langOf = (i: number) => (over.has(i) && over.get(i)!.lang !== undefined ? over.get(i)!.lang : map.elements[i]?.lang);
  const parentOf = (i: number): number | null => tree.parent.get(i) ?? null;
  const ancestors = (i: number) => {
    const out: number[] = [];
    for (let p = parentOf(i); p !== null; p = parentOf(p)) out.push(p);
    return out;
  };
  const kids = (i: number) => (tree.kids.get(i) ?? []).filter((k) => !gone.has(k));
  const decorative = (i: number) => [i, ...ancestors(i)].some((a) => over.get(a)?.decorative);

  // A tag's own words: its full text, plus the text of tags merged into it by pending edits
  const merged = new Map<number, number[]>();
  for (const [a, into] of gone) merged.set(into, [...(merged.get(into) ?? []), a]);
  const ownText = (e: TagElement): string =>
    [e.say ?? e.text ?? "", ...(merged.get(e.i) ?? []).map((a) => ownText(map.elements[a]))].filter(Boolean).join(" ");

  const out: Utterance[] = [];
  const read = new Set<number>(); // tags already read (a tag on two pages is read once)
  const open: number[] = []; // the lists and tables being read, outermost first

  const say = (el: number, page: number, text: string) => {
    const t = text.replace(/\s+/g, " ").trim();
    if (!t) return;
    let lang: string | undefined;
    for (const a of [el, ...ancestors(el)]) { const l = langOf(a); if (l) { lang = l; break; } }
    out.push({ el, page, text: t, ...(lang ? { lang } : {}) });
  };

  const describeGroup = (g: number): string => {
    const role = roleOf(g);
    const n = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
    if (role === "L") return `List, ${n(kids(g).filter((k) => roleOf(k) === "LI").length, "item")}`;
    if (role === "Table") {
      const rows: number[] = [];
      const collect = (i: number) => kids(i).forEach((k) => (roleOf(k) === "TR" ? rows.push(k) : ["THead", "TBody", "TFoot"].includes(roleOf(k)) && collect(k)));
      collect(g);
      const cols = Math.max(0, ...rows.map((r) => kids(r).length));
      return `Table, ${n(rows.length, "row")}, ${n(cols, "column")}`;
    }
    return "";
  };

  for (let p = 1; p <= map.pages.length; p++) {
    for (const e of pageOrder(map, p, ops)) {
      if (read.has(e.i) || decorative(e.i)) continue;
      // Alt or read-aloud text on a tag replaces everything inside it: read it once, at its first piece of content
      const up = ancestors(e.i);
      const replacing = [...up].reverse().find((a) => altOf(a) || actualOf(a));
      const speaker = replacing ?? e.i;
      if (read.has(speaker)) { read.add(e.i); continue; }

      // Leaving lists and tables this tag isn't in; entering ones it is in
      const groups = [...up].reverse().filter((a) => roleOf(a) === "L" || roleOf(a) === "Table");
      while (open.length && !groups.includes(open[open.length - 1])) {
        const g = open.pop()!;
        say(g, p, roleOf(g) === "L" ? "End of list" : "End of table");
      }
      for (const g of groups) {
        if (!open.includes(g)) { open.push(g); say(g, p, describeGroup(g)); }
      }

      read.add(e.i);
      read.add(speaker);
      const role = roleOf(speaker);
      const alt = altOf(speaker);
      const actual = actualOf(speaker);
      const words = alt || actual || ownText(map.elements[speaker]);
      const h = HEADING.exec(role);
      let text: string;
      if (role === "Figure") text = alt ? `Figure: ${alt}` : "Figure, no alt text";
      else if (role === "Formula") text = alt ? `Formula: ${alt}` : actual ? `Formula: ${actual}` : "Formula, no alt text";
      else if (h) text = `Heading level ${h[1]}, ${words}`;
      else if (role === "H") text = `Heading, ${words}`;
      else if (role === "Link") text = `Link: ${words || map.elements[speaker].name || ""}`;
      else if (role === "Form") text = `Form field: ${words || map.elements[speaker].name || "no name"}`;
      else if (role === "TH") text = `Header: ${words}`;
      else text = words;
      say(speaker, p, text);
    }
  }
  while (open.length) {
    const g = open.pop()!;
    say(g, map.pages.length, roleOf(g) === "L" ? "End of list" : "End of table");
  }
  return out;
}

/** Long text split at sentence ends into pieces a browser voice reads reliably (some stop after ~15 seconds). */
export function chunks(text: string, max = 220): string[] {
  const out: string[] = [];
  let rest = text.trim();
  while (rest.length > max) {
    const slice = rest.slice(0, max);
    const cut = Math.max(slice.lastIndexOf(". "), slice.lastIndexOf("? "), slice.lastIndexOf("! "), slice.lastIndexOf("; "));
    const at = cut > max / 3 ? cut + 1 : slice.lastIndexOf(" ") > 0 ? slice.lastIndexOf(" ") : max;
    out.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) out.push(rest);
  return out;
}

/** Where reading "from here" starts for a tag: its own words; for a tag with none of its own (a list, a section),
 * the first words read inside it. -1 when nothing in it is read. */
export function startFor(script: Utterance[], map: TagMap, el: number): number {
  const own = script.findIndex((u) => u.el === el);
  if (own >= 0) return own;
  const within = (i: number) => { for (let p: number | null = i; p !== null && p !== undefined; p = map.elements[p]?.parent ?? null) if (p === el) return true; return false; };
  return script.findIndex((u) => within(u.el));
}
