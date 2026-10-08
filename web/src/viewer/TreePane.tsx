import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Finding, TagEdit, TagElement, TagMap } from "../api";
import { errorMessage } from "../components/ui";
import { ROLE_NAMES, absorbedBy, overrides, pageOrder, roleClass } from "./draft";
import { loadVersion } from "./pdf";
import { checkElementRole } from "./roles";
import { worstSeverity } from "./PageView";

type Props = {
  side: "left" | "right";
  jobId: string;
  number: number; // the version whose tree is shown (the other pane's)
  sourceLabel: string;
  picker: ReactNode;
  findings: (Finding & { element?: number })[];
  ops: TagEdit[]; // pending edits shown in the tree (only for the editable right-hand version)
  selected: number | null;
  onSelect: (el: TagElement) => void;
  onHover: (i: number | null) => void;
  follow: { page: number; y: number } | null; // where the document beside the tree is scrolled to
};

// role/text: how a row will look once pending changes are saved (a list taken apart into one tag per item)
type Row = { el: TagElement; depth: number; hasKids: boolean; role?: string; text?: string };

const SNIPPET = 70;

function snippet(el: TagElement, alt?: string): string {
  const t = (alt ?? el.alt) ? `alt: ${alt ?? el.alt}` : el.actual_text ?? el.text ?? "";
  return t.length > SNIPPET ? `${t.slice(0, SNIPPET)}…` : t;
}

/** The tag structure tree of a version, for finding your way around it; clicking a tag shows it on its page. */
export function TreePane({ side, jobId, number, sourceLabel, picker, findings, ops, selected, onSelect, onHover, follow }: Props) {
  const [map, setMap] = useState<TagMap | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [focus, setFocus] = useState<number | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setMap(null);
    setError(null);
    loadVersion(jobId, number).then(
      (d) => {
        if (!live) return;
        setMap(d.tagmap);
        // Start with the top two levels open
        const start = new Set<number>();
        for (const e of d.tagmap.elements) if (e.parent === null || d.tagmap.elements[e.parent]?.parent === null) start.add(e.i);
        setOpen(start);
      },
      (e) => { if (live) setError(e); },
    );
    return () => { live = false; };
  }, [jobId, number]);

  const over = useMemo(() => overrides(ops), [ops]);
  const gone = useMemo(() => absorbedBy(ops), [ops]);

  const sevOf = useMemo(() => {
    const by = new Map<number, Finding[]>();
    for (const f of findings) if (f.element != null) by.set(f.element, [...(by.get(f.element) ?? []), f]);
    return by;
  }, [findings]);

  // Reading-order position of each tag with content of its own, per page, with pending moves applied
  const order = useMemo(() => {
    const out = new Map<number, { page: number; n: number }[]>();
    if (!map) return out;
    map.pages.forEach((_, p) =>
      pageOrder(map, p + 1, ops).forEach((e, n) => out.set(e.i, [...(out.get(e.i) ?? []), { page: p + 1, n: n + 1 }])));
    return out;
  }, [map, ops]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    if (!map) return out;
    const kidsOf = (e: TagElement) => e.kids.filter((k) => map.elements[k] && !gone.has(k));
    const walk = (e: TagElement, depth: number) => {
      const next = over.get(e.i)?.role;
      if (e.role === "L" && next && checkElementRole(map, e, next).action === "collapse_list") {
        // Preview of the save: each item becomes a tag of the new type, nested lists follow it
        let first = true;
        for (const k of kidsOf(e)) {
          const kid = map.elements[k];
          if (kid.role !== "LI") { walk(kid, depth); continue; }
          const parts = kidsOf(kid).map((x) => map.elements[x]);
          const blocks = parts.flatMap((p) => (p.role === "LBody" || p.role === "Lbl" ? kidsOf(p).map((x) => map.elements[x]) : [p]))
            .filter((x) => x.role === "L" || x.role === "Table" || /^H\d?$|^P$/.test(x.role));
          out.push({ el: first ? e : kid, depth, hasKids: false, role: next, text: kid.actual_text ?? kid.text });
          first = false;
          for (const b of blocks) walk(b, depth);
        }
        return;
      }
      const kids = kidsOf(e);
      out.push({ el: e, depth, hasKids: kids.length > 0 });
      if (open.has(e.i)) for (const k of kids) walk(map.elements[k], depth + 1);
    };
    for (const e of map.elements) if (e.parent === null && !gone.has(e.i)) walk(e, 0);
    return out;
  }, [map, open, gone, over]);

  // Selecting a tag elsewhere (on the page, or in the reading-order list) opens the tree down to it
  useEffect(() => {
    if (!map || selected === null || !map.elements[selected]) return;
    const need: number[] = [];
    for (let p = map.elements[selected].parent; p !== null && p !== undefined; p = map.elements[p]?.parent ?? null) need.push(p);
    if (need.some((p) => !open.has(p))) setOpen((o) => new Set([...o, ...need]));
    setFocus(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, selected]);

  useEffect(() => {
    if (selected === null) return;
    bodyRef.current?.querySelector<HTMLElement>(`[data-i="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected, rows]);

  // Follow the document: bring the first tag at the top of the visible part of the page to the top of the
  // tree (or the closest open tag containing it)
  useEffect(() => {
    if (!map || !follow) return;
    let best: TagElement | null = null;
    let bestY = Infinity;
    for (const e of map.elements) {
      if (!e.own || gone.has(e.i)) continue;
      for (const b of e.boxes) {
        if (b.page !== follow.page || b.bbox[3] < follow.y) continue;
        if (b.bbox[1] < bestY) { bestY = b.bbox[1]; best = e; }
      }
    }
    const body = bodyRef.current;
    if (!best || !body) return;
    for (let i: number | null = best.i; i !== null && i !== undefined; i = map.elements[i]?.parent ?? null) {
      const row = body.querySelector<HTMLElement>(`[data-i="${i}"]`);
      if (row) {
        body.scrollTop = Math.max(0, row.offsetTop - 8);
        return;
      }
    }
  }, [follow, map, gone]);

  const toggle = (i: number, to?: boolean) =>
    setOpen((o) => {
      const n = new Set(o);
      if (to ?? !n.has(i)) n.add(i);
      else n.delete(i);
      return n;
    });

  const focusRow = (i: number) => {
    setFocus(i);
    bodyRef.current?.querySelector<HTMLElement>(`[data-i="${i}"]`)?.focus();
  };

  function onKey(e: KeyboardEvent, r: Row, at: number) {
    const go = (k: number) => { if (rows[k]) focusRow(rows[k].el.i); };
    switch (e.key) {
      case "ArrowDown": go(at + 1); break;
      case "ArrowUp": go(at - 1); break;
      case "Home": go(0); break;
      case "End": go(rows.length - 1); break;
      case "ArrowRight":
        if (r.hasKids && !open.has(r.el.i)) toggle(r.el.i, true);
        else if (r.hasKids) go(at + 1);
        break;
      case "ArrowLeft":
        if (r.hasKids && open.has(r.el.i)) toggle(r.el.i, false);
        else if (r.el.parent !== null) focusRow(r.el.parent);
        break;
      case "Enter":
      case " ":
        onSelect(r.el);
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  const all = () => setOpen(new Set(map?.elements.filter((e) => e.kids.length).map((e) => e.i) ?? []));
  const roving = focus !== null && rows.some((r) => r.el.i === focus) ? focus : rows[0]?.el.i;

  return (
    <section className="pv-pane" aria-label={`${side === "left" ? "Left" : "Right"}: tag tree of ${sourceLabel}`}>
      <div className="pv-head">
        {picker}
        <span className="small muted">Tree of {sourceLabel}{ops.length ? ", with your unsaved changes" : ""}</span>
        <span className="pv-tree-btns">
          <button className="linkbtn small" onClick={all} disabled={!map}>Expand all</button>
          <button className="linkbtn small" onClick={() => setOpen(new Set())} disabled={!map}>Collapse all</button>
        </span>
      </div>
      <div className="pv-body pv-tree-body" ref={bodyRef} onMouseLeave={() => onHover(null)}>
        {error ? (
          <p className="pv-msg" role="alert">{errorMessage(error)}</p>
        ) : !map ? (
          <p className="muted pv-msg" role="status">Loading tags…</p>
        ) : rows.length === 0 ? (
          <p className="muted pv-msg">This version has no tags.</p>
        ) : (
          <ul className="pv-tree" role="tree" aria-label={`Tag tree of ${sourceLabel}`}>
            {rows.map((r, at) => {
              const o = over.get(r.el.i) ?? {};
              const role = r.role ?? o.role ?? r.el.role;
              const fs = sevOf.get(r.el.i) ?? [];
              const sev = worstSeverity(fs);
              const pages = [...new Set(r.el.boxes.map((b) => b.page))].sort((a, b) => a - b);
              const pos = order.get(r.el.i);
              const text = r.text !== undefined ? snippet({ ...r.el, alt: undefined, actual_text: undefined, text: r.text }) : snippet(r.el, o.alt);
              const tip = [
                `${ROLE_NAMES[role] ?? role}${role !== r.el.role ? ` (was ${r.el.role})` : ""}`,
                pos?.length ? `Reading order: ${pos.map((p) => `${p.n} on page ${p.page}`).join(", ")}` : "No content of its own",
                ...fs.map((f) => `${f.severity}: ${f.issue}`),
              ].join("\n");
              return (
                <li key={r.el.i} role="treeitem" aria-level={r.depth + 1} aria-selected={selected === r.el.i}
                  aria-expanded={r.hasKids ? open.has(r.el.i) : undefined}
                  className={`pv-tree-row${selected === r.el.i ? " selected" : ""}${o.decorative ? " decorative" : ""}`}
                  style={{ paddingLeft: 4 + r.depth * 14 }}
                  data-i={r.el.i} tabIndex={roving === r.el.i ? 0 : -1} title={tip}
                  onKeyDown={(e) => onKey(e, r, at)}
                  onFocus={() => { setFocus(r.el.i); onHover(r.el.i); }}
                  onMouseEnter={() => onHover(r.el.i)}
                  onClick={() => { setFocus(r.el.i); onSelect(r.el); }}>
                  {r.hasKids ? (
                    <button className="pv-tree-tog" tabIndex={-1} aria-hidden="true"
                      onClick={(e) => { e.stopPropagation(); toggle(r.el.i); }}>{open.has(r.el.i) ? "▾" : "▸"}</button>
                  ) : <span className="pv-tree-tog" aria-hidden="true" />}
                  <span className={`pv-tree-role ${roleClass(role)}`}>{role}</span>
                  {sev && <span className={`pv-dot sev-${sev}`} role="img" aria-label={`${fs.length} ${sev} finding${fs.length > 1 ? "s" : ""}`} />}
                  <span className="pv-tree-text">{o.decorative ? "decorative (pending)" : text}</span>
                  {pages.length > 0 && (
                    <span className="pv-tree-page small muted">
                      p.{pages.length > 1 ? `${pages[0]}–${pages[pages.length - 1]}` : pages[0]}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
