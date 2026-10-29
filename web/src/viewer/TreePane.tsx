import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Finding, MovePosition, TagEdit, TagElement, TagMap } from "../api";
import { errorMessage } from "../components/ui";
import { subtree } from "./isolate";
import { ROLE_NAMES, absorbedBy, draftTree, editMarks, overrides, pageOrder, roleClass, tagsOnPage } from "./draft";
import { tableReplaced } from "./tablegrid";
import { contentMoves, splitKeptText } from "./areaPreview";
import { expand } from "./made";
import { centered, keptInView } from "./scroll";
import { loadTagmap, watchVersion, type LoadState } from "./pdf";
import { tagsLoading } from "../components/loadWords";
import { LoadProgress } from "../components/LoadProgress";
import { checkElementRole, checkMove } from "./roles";
import { worstSeverity } from "./PageView";
import { rowTitle, rowWords } from "./fullText";

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
  // Editable versions: drag a tag (or Alt+arrow keys) to move it; only moves that keep the tags valid are allowed
  onMove?: (el: TagElement, target: TagElement, position: MovePosition) => void;
  onUndoMove?: (el: TagElement) => void; // take back a tag's unsaved moves
  page?: number | null; // one-page view: only this page's tags (and the groups around them)
  tools?: ReactNode; // viewing controls shown at the end of the header row
  isolated?: number | null; // a tag isolated: the other rows are veiled
  picked?: number[]; // other tags selected along with `selected`
  // Ctrl or Cmd with a click adds a tag or takes it out; Shift takes the run to it, in the order the rows are shown
  onPick?: (el: TagElement, how: "toggle" | "range", visible: number[]) => void;
  onMenu?: (el: TagElement, x: number, y: number) => void; // a right-click, Shift+F10 or the Menu key on a row
  onReadAll?: (el: TagElement) => void; // "Read all" by a row's cut-off words: the tag's details, where it's in full
};

// role/text: how a row will look once pending changes are saved (a list taken apart into one tag per item)
// head: in the reading-order list, the first tag of a page (the page's heading is drawn above it)
// `ghost`: a row of an unsaved change's preview that stands for part of `el` (a list built from it): drawn, and
// clicking it selects `el`, but it isn't a tag of its own yet, so the keys pass over it
type Row = { el: TagElement; depth: number; hasKids: boolean; role?: string; text?: string; head?: number; page?: number; ghost?: number };

/** The tag structure tree of a version, for finding your way around it; clicking a tag shows it on its page. */
export function TreePane({ side, jobId, number, sourceLabel, picker, findings, ops: given, selected, onSelect, onHover, follow, onMove,
  onUndoMove, page = null, tools, picked, onPick, onMenu, onReadAll, isolated = null }: Props) {
  // The tags as a tree, or as the list a screen reader goes through (the same pending changes either way)
  const [show, setShow] = useState<"tree" | "order">("tree");
  const [helpOpen, setHelpOpen] = useState(() => { try { return localStorage.getItem("pv-tree-help") === "1"; } catch { return false; } });
  const [loaded, setMap] = useState<TagMap | null>(null);
  // With the unsaved changes, the tags they make are tags of the tree (made.ts, #692)
  const draft = useMemo(() => (loaded ? expand(loaded, given) : null), [loaded, given]);
  const map = draft?.map ?? null;
  const ops = draft?.ops ?? given;
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [focus, setFocus] = useState<number | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [load, setLoad] = useState<LoadState | null>(null);

  useEffect(() => {
    let live = true;
    setMap(null);
    setError(null);
    setLoad(null);
    // The tag map alone: the tree shows while the PDF is still on its way (#579)
    loadTagmap(jobId, number).then(
      (tagmap) => {
        if (!live) return;
        setMap(tagmap);
        // Start with the top two levels open
        const start = new Set<number>();
        for (const e of tagmap.elements) if (e.parent === null || tagmap.elements[e.parent]?.parent === null) start.add(e.i);
        setOpen(start);
      },
      (e) => { if (live) setError(e); },
    );
    const unwatch = watchVersion(jobId, number, setLoad);
    return () => { live = false; unwatch(); };
  }, [jobId, number]);

  const over = useMemo(() => overrides(ops, map), [ops, map]);
  const tree = useMemo(() => (map ? draftTree(map, ops) : null), [map, ops]);
  // Tags no longer in the tree: merged into another, or deleted
  // Tags made by pending edits (an area of untagged content, a split), and tags whose content all moves away (#427)
  const kept = useMemo(() => (map ? splitKeptText(map, ops) : new Map<number, string>()), [map, ops]);
  const emptied = useMemo(() => (map ? contentMoves(map, ops).emptied : new Set<number>()), [map, ops]);
  const gone = useMemo(() => new Set([...absorbedBy(ops).keys(), ...(tree?.deleted ?? []), ...emptied,
    ...(map ? ops.flatMap((o) => (o.op === "build_table" || o.op === "rebuild_table" ? tableReplaced(map, o) : [])) : [])]),
  [ops, tree, map, emptied]);
  const isolatedTags = useMemo(() => (isolated !== null && map ? subtree(map, isolated) : null), [isolated, map]);
  const edited = useMemo(() => (map ? editMarks(map, ops) : new Map<number, string[]>()), [map, ops]);
  const [drag, setDrag] = useState<number | null>(null);
  const [drop, setDrop] = useState<{ i: number; position: MovePosition; ok: boolean } | null>(null);
  const [moveNote, setMoveNote] = useState("");

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

  // Only documents that came tagged, and kept their tags, say which tags were their own
  const anyOrig = useMemo(() => !!map?.elements.some((e) => e.orig), [map]);
  const onThisPage = useMemo(() => (map && page ? tagsOnPage(map, page) : null), [map, page]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    if (!map) return out;
    if (show === "order") {
      // Tags with content of their own, page by page, in the order they're read
      const pages = page ? [page] : map.pages.map((_, p) => p + 1);
      for (const p of pages) {
        pageOrder(map, p, ops).forEach((el, n) => out.push({ el, depth: 0, hasKids: false, page: p, ...(n === 0 ? { head: p } : {}) }));
      }
      return out;
    }
    const kidsOf = (e: TagElement) => (tree?.kids.get(e.i) ?? e.kids).filter(
      (k) => map.elements[k] && !gone.has(k) && (!onThisPage || onThisPage.has(k)));
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
      out.push({ el: e, depth, hasKids: kids.length > 0, ...(kept.has(e.i) ? { text: kept.get(e.i) } : {}) });
      // One page's tags are few enough to show open
      if (open.has(e.i) || onThisPage) for (const k of kids) walk(map.elements[k], depth + 1);
    };
    for (const i of tree?.kids.get(-1) ?? map.elements.filter((e) => e.parent === null).map((e) => e.i)) {
      if (!gone.has(i)) walk(map.elements[i], 0);
    }
    return out;
  }, [map, open, gone, over, tree, show, page, ops, onThisPage, kept]);

  // --- moving tags (editable versions) -----------------------------------------------------------
  const roleOf = (i: number | null | undefined) => (i === null || i === undefined || !map ? null : over.get(i)?.role ?? map.elements[i].role);
  const parentOf = (i: number) => (tree?.parent.get(i) ?? map?.elements[i]?.parent ?? null);
  const inside = (i: number, ancestor: number) => {
    for (let p: number | null = i; p !== null; p = parentOf(p)) if (p === ancestor) return true;
    return false;
  };
  /** Whether `el` can go before/after `target`, or in it (its last child), and if not, why. */
  const verdict = (el: number, target: number, position: MovePosition = "before") => {
    if (el === target || inside(target, el)) return { ok: false, reason: "A tag can't move inside itself." };
    const from = parentOf(el), to = position === "in" ? target : parentOf(target);
    return checkMove(roleOf(el)!, roleOf(from), roleOf(to), from === to);
  };
  const tryMove = (el: number, target: number, position: MovePosition) => {
    if (!map || !onMove) return;
    const v = verdict(el, target, position);
    if (!v.ok) { setMoveNote(`Can't move it there. ${v.reason}`); return; }
    onMove(map.elements[el], map.elements[target], position);
    setMoveNote(`Moved ${roleOf(el)} ${position === "in" ? "into" : position} ${roleOf(target)}. Not saved yet.`);
  };
  const siblings = (i: number) => (tree?.kids.get(parentOf(i) ?? -1) ?? []).filter((k) => !gone.has(k));

  // Selecting a tag elsewhere (on the page, or in the reading-order list) opens the tree down to it
  useEffect(() => {
    if (!map || selected === null || !map.elements[selected]) return;
    const need: number[] = [];
    for (let p = map.elements[selected].parent; p !== null && p !== undefined; p = map.elements[p]?.parent ?? null) need.push(p);
    if (need.some((p) => !open.has(p))) setOpen((o) => new Set([...o, ...need]));
    setFocus(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, selected]);

  // A tag selected elsewhere (on the page) is brought to the middle of the tree. One chosen in the tree itself
  // (a click, the arrow keys) is only kept in view, so the row doesn't jump away from the pointer
  const shownSelected = useRef<number | null>(null);
  useEffect(() => {
    const body = bodyRef.current;
    const row = selected === null ? null : body?.querySelector<HTMLElement>(`[data-i="${selected}"]`);
    if (!body || !row) return;  // not drawn yet: the tree is still opening down to it
    const fresh = shownSelected.current !== selected;
    shownSelected.current = selected;
    body.scrollTop = fresh && !body.contains(document.activeElement) ? centered(row.offsetTop, row.offsetHeight, body.clientHeight)
      : keptInView(row.offsetTop, row.offsetHeight, body.scrollTop, body.clientHeight);
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
    const go = (k: number) => {
      const step = k < at ? -1 : 1;
      while (rows[k]?.ghost) k += step; // a preview row isn't a tag of its own yet
      if (rows[k]) focusRow(rows[k].el.i);
    };
    if (onMove && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      // Move the tag before the previous sibling / after the next one
      const sib = show === "order" ? rows.filter((x) => x.page === r.page).map((x) => x.el.i) : siblings(r.el.i);
      const k = sib.indexOf(r.el.i) + (e.key === "ArrowUp" ? -1 : 1);
      if (k >= 0 && k < sib.length) tryMove(r.el.i, sib[k], e.key === "ArrowUp" ? "before" : "after");
      else setMoveNote(show === "order" ? (e.key === "ArrowUp" ? "It's already first on its page." : "It's already last on its page.")
        : e.key === "ArrowUp" ? "It's already first in its group." : "It's already last in its group.");
      e.preventDefault();
      return;
    }
    if (onMove && show === "tree" && e.altKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      // Into the tag before it (at its end), or out of its own tag (just after it)
      const sib = siblings(r.el.i), k = sib.indexOf(r.el.i), up = parentOf(r.el.i);
      if (e.key === "ArrowRight") {
        if (k > 0) {
          tryMove(r.el.i, sib[k - 1], "in");
          setOpen((o) => new Set([...o, sib[k - 1]]));
        } else setMoveNote("There's no tag before it, in its group, to move it into.");
      } else if (up !== null && parentOf(up) !== null) tryMove(r.el.i, up, "after");
      else setMoveNote("It's already at the top level.");
      e.preventDefault();
      return;
    }
    if (onUndoMove && e.altKey && (e.key === "Backspace" || e.key === "Delete")) {
      if (edited.get(r.el.i)?.includes("moved")) undoMove(r.el);
      e.preventDefault();
      return;
    }
    if (onMenu && (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10"))) {
      const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
      onMenu(r.el, box.left + 24, box.bottom);
      e.preventDefault();
      return;
    }
    const shown = () => rows.map((x) => x.el.i);
    if (onPick && e.shiftKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      // Shift with an arrow takes the next row into the selection
      const next = rows[at + (e.key === "ArrowDown" ? 1 : -1)];
      if (next) { focusRow(next.el.i); onPick(next.el, "range", shown()); }
      e.preventDefault();
      return;
    }
    if (onPick && (e.ctrlKey || e.metaKey) && (e.key === " " || e.key === "Enter")) {
      onPick(r.el, "toggle", shown());
      e.preventDefault();
      return;
    }
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
        else if (show === "tree" && r.el.parent !== null) focusRow(r.el.parent);
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

  const undoMove = (el: TagElement) => {
    onUndoMove?.(el);
    setMoveNote(`Move undone: ${roleOf(el.i)} is back where it was.`);
  };

  const all = () => setOpen(new Set(map?.elements.filter((e) => e.kids.length).map((e) => e.i) ?? []));
  const roving = focus !== null && rows.some((r) => r.el.i === focus) ? focus : rows[0]?.el.i;

  return (
    <section className="pv-pane" aria-label={`${side === "left" ? "Left" : "Right"}: tag tree of ${sourceLabel}`}>
      <div className="pv-head">
        {picker}
        <label htmlFor={`tree-show-${side}`} className="visually-hidden">Show the tags as</label>
        <select id={`tree-show-${side}`} value={show} onChange={(e) => setShow(e.target.value as "tree" | "order")}>
          <option value="tree">Tag tree</option>
          <option value="order">Reading order</option>
        </select>
        {/* What the list shows and how to move tags: folded away, since the header row is better spent on the tags */}
        <details className="pv-tree-help small muted" open={helpOpen}
          onToggle={(e) => { const o = e.currentTarget.open; setHelpOpen(o); try { localStorage.setItem("pv-tree-help", o ? "1" : "0"); } catch { /* per-viewer preference only */ } }}>
          <summary>About<span className="visually-hidden"> this {show === "tree" ? "tag tree" : "list"}</span></summary>
          {show === "tree" ? "Tree" : "Reading order"} of {sourceLabel}{page ? `, page ${page}` : ""}
          {ops.length ? ", with your unsaved changes" : ""}
          {onMove ? " · drag a tag, or Alt+↑/↓, to move it; drop it on the middle of a tag, or Alt+→, to put it inside (into a section, say), Alt+← to take it out" : ""}{onUndoMove ? "; Alt+Delete undoes its move" : ""}
        </details>
        {show === "tree" && !page && (
          <span className="pv-tree-btns">
            <button className="linkbtn small" onClick={all} disabled={!map}>Expand all</button>
            <button className="linkbtn small" onClick={() => setOpen(new Set())} disabled={!map}>Collapse all</button>
          </span>
        )}
        {tools}
      </div>
      <div className="pv-body pv-tree-body" ref={bodyRef} onMouseLeave={() => onHover(null)}>
        {error ? (
          <p className="pv-msg" role="alert">{errorMessage(error)}</p>
        ) : !map ? (
          // The same download as the pages beside it, which announce it: shown here, not said twice
          <LoadProgress className="pv-loading" quiet {...tagsLoading(load)} />
        ) : rows.length === 0 ? (
          <p className="muted pv-msg">{page ? `No tags on page ${page}.` : "This version has no tags."}</p>
        ) : (
          <>
          {anyOrig && <p className="small muted pv-tree-key"><span className="pv-tree-orig">original</span> on a blue row: the
            tag was in the document as it was uploaded. The others were added by this service.</p>}
          {onMove && <p className="small pv-tree-note" role="status" aria-live="polite">{moveNote}</p>}
          <ul className="pv-tree" role={show === "tree" ? "tree" : "listbox"} aria-multiselectable={onPick ? true : undefined}
            aria-label={`${show === "tree" ? "Tag tree" : "Reading order"} of ${sourceLabel}${page ? `, page ${page}` : ""}`}>
            {rows.map((r, at) => {
              const o = over.get(r.el.i) ?? {};
              const role = r.role ?? o.role ?? r.el.role;
              const fs = r.ghost ? [] : sevOf.get(r.el.i) ?? [];
              const sev = worstSeverity(fs);
              const pages = [...new Set(r.el.boxes.map((b) => b.page))].sort((a, b) => a - b);
              const pos = order.get(r.el.i);
              const words = o.decorative ? { full: "", short: "", cut: false }
                : r.text !== undefined
                  ? rowWords({ ...r.el, alt: undefined, actual_text: undefined, say: undefined, text: r.text })
                : rowWords(r.el, o.alt);
              const text = words.short;
              const marks = r.ghost ? undefined : edited.get(r.el.i); // said once, on the row a preview starts at
              // The words in full when the row cuts them (#436)
              const name = `${ROLE_NAMES[role] ?? role}${role !== r.el.role ? ` (was ${r.el.role})` : ""}`;
              const tip = rowTitle(name, words, [
                ...(marks ? [`Unsaved: ${marks.join(", ")}`] : []),
                ...(anyOrig ? [r.el.orig ? "From the original document" : "Added by this service"] : []),
                pos?.length ? `Reading order: ${pos.map((p) => `${p.n} on page ${p.page}`).join(", ")}` : "No content of its own",
                ...fs.map((f) => `${f.severity}: ${f.issue}`),
              ]);
              return (
                <Fragment key={show === "order" ? `${r.page}-${r.el.i}` : r.ghost ? `${r.el.i}-${r.ghost}` : r.el.i}>
                {r.head !== undefined && !page && <li role="presentation" className="pv-tree-head small">Page {r.head}</li>}
                <li role={show === "tree" ? "treeitem" : "option"} aria-level={show === "tree" ? r.depth + 1 : undefined}
                  aria-selected={selected === r.el.i || !!picked?.includes(r.el.i)}
                  aria-expanded={r.hasKids && !onThisPage ? open.has(r.el.i) : undefined}
                  className={`pv-tree-row${isolatedTags && !isolatedTags.has(r.el.i) ? " veiled" : ""}${r.ghost ? " ghost" : ""}${selected === r.el.i || picked?.includes(r.el.i) ? " selected" : ""}${o.decorative ? " decorative" : ""}${marks ? " edited" : ""}${r.el.orig ? " orig" : ""}`}
                  style={{ paddingLeft: 4 + r.depth * 14 }}
                  data-i={r.ghost ? undefined : r.el.i} tabIndex={!r.ghost && roving === r.el.i ? 0 : -1} title={tip}
                  aria-hidden={r.ghost ? true : undefined}
                  onKeyDown={(e) => onKey(e, r, at)}
                  onFocus={() => { setFocus(r.el.i); onHover(r.el.i); }}
                  onMouseEnter={() => onHover(r.el.i)}
                  onClick={(e) => {
                    setFocus(r.el.i);
                    const ids = rows.map((x) => x.el.i);
                    if (onPick && (e.ctrlKey || e.metaKey)) onPick(r.el, "toggle", ids);
                    else if (onPick && e.shiftKey) onPick(r.el, "range", ids);
                    else onSelect(r.el);
                  }}
                  onContextMenu={(e) => { if (onMenu) { e.preventDefault(); setFocus(r.el.i); onMenu(r.el, e.clientX, e.clientY); } }}
                  draggable={!!onMove && r.el.parent !== null}
                  onDragStart={(e) => { setDrag(r.el.i); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(r.el.i)); }}
                  onDragEnd={() => { setDrag(null); setDrop(null); }}
                  onDragOver={(e) => {
                    if (drag === null) return;
                    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
                    // The middle of a tag that can hold it puts it inside; above and below it, beside
                    const y = (e.clientY - box.top) / box.height;
                    const position: MovePosition = y > 0.25 && y < 0.75 && verdict(drag, r.el.i, "in").ok ? "in"
                      : y < 0.5 ? "before" : "after";
                    const v = verdict(drag, r.el.i, position);
                    if (!drop || drop.i !== r.el.i || drop.position !== position || drop.ok !== v.ok) {
                      setDrop({ i: r.el.i, position, ok: v.ok });
                      setMoveNote(v.ok ? "" : `Can't drop here. ${v.reason}`);
                    }
                    if (v.ok) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (drag !== null && drop) tryMove(drag, r.el.i, drop.position);
                    setDrag(null);
                    setDrop(null);
                  }}
                  data-drop={drop && drop.i === r.el.i ? `${drop.position}${drop.ok ? "" : "-no"}` : undefined}>
                  {show === "order" ? (
                    <span className="pv-tree-no small muted" aria-hidden="true">{pos?.find((x) => x.page === r.page)?.n ?? ""}</span>
                  ) : r.hasKids && !onThisPage ? (
                    <button className="pv-tree-tog" tabIndex={-1} aria-hidden="true"
                      onClick={(e) => { e.stopPropagation(); toggle(r.el.i); }}>{open.has(r.el.i) ? "▾" : "▸"}</button>
                  ) : <span className="pv-tree-tog" aria-hidden="true" />}
                  <span className={`pv-tree-role ${roleClass(role)}`}>{role}</span>
                  {sev && <span className={`pv-dot sev-${sev}`} role="img" aria-label={`${fs.length} ${sev} finding${fs.length > 1 ? "s" : ""}`} />}
                  {/* Cut short on the row; a screen reader is given the whole of it (#436) */}
                  <span className="pv-tree-text" aria-hidden={words.cut || undefined}>
                    {o.decorative ? "decorative (pending)" : text}</span>
                  {words.cut && <span className="visually-hidden">{words.full}</span>}
                  {words.cut && onReadAll && !r.ghost && (
                    // Out of the tab order, like the row's other buttons: from the keyboard, the row's menu (Shift+F10)
                    // has the tag's details
                    <button className="linkbtn small pv-tree-more" tabIndex={-1} aria-hidden="true"
                      title="Read all of it in the tag's details"
                      onClick={(e) => { e.stopPropagation(); setFocus(r.el.i); onReadAll(r.el); }}>Read all</button>
                  )}
                  {marks && <span className="pv-tree-edit small">{marks.length > 1 ? `${marks.length} changes` : marks[0]}
                    <span className="visually-hidden">{marks.length > 1 ? `: ${marks.join(", ")}` : ""}, not saved</span></span>}
                  {onUndoMove && marks?.includes("moved") && (
                    <button className="pv-tree-undo" tabIndex={-1} title="Undo this move (Alt+Delete)"
                      onClick={(e) => { e.stopPropagation(); undoMove(r.el); }}>
                      <span aria-hidden="true">↩</span><span className="visually-hidden">Undo the move of {role}{text ? `: ${text}` : ""}</span>
                    </button>
                  )}
                  {r.el.orig && <span className="pv-tree-orig small" title="This tag was in the document as it was uploaded">original</span>}
                  {pages.length > 0 && !r.ghost && (
                    <span className="pv-tree-page small muted">
                      p.{pages.length > 1 ? `${pages[0]}–${pages[pages.length - 1]}` : pages[0]}
                    </span>
                  )}
                </li>
                </Fragment>
              );
            })}
          </ul>
          </>
        )}
      </div>
    </section>
  );
}
