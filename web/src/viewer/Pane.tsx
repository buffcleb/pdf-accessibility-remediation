import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { DocVersion, Finding, TagEdit } from "../api";
import { checkLink } from "../checks";
import { errorMessage, fmtNum } from "../components/ui";
import { absorbedBy, deletedTags, overrides, pageOrder, type Area } from "./draft";
import { chooseAt, tagsAt, type LastHit } from "./hit";
import { holes, subtree } from "./isolate";
import { builtPreview, type Box } from "./outline";
import { tablePreview, tableReplaced } from "./tablegrid";
import { splitPreview } from "./figure";
import { contentMoves, newTags, splitKeeps } from "./areaPreview";
import { loadVersion, watchVersion, type LoadState, type PaneData } from "./pdf";
import { LoadProgress } from "../components/LoadProgress";
import { LARGE_NOTE, READING_TAGS, downloading, drawingPage, fraction, isLarge } from "../components/loadWords";
import { PageView, type OverlayItem } from "./PageView";
import type { Spot } from "./spot";

export type OverlayMode = "flagged" | "all" | "order" | "off";
// only: in one-page view, the page shown (1-based); it sits at the top of the pane, whatever its offset says
export type PaneLayout = { offsets: number[]; heights: number[]; scale: number; only: number | null };

export const PAGE_TOP = 12;

export const PAGE_GAP = 16;

type Props = {
  side: "left" | "right";
  jobId: string;
  versions: DocVersion[];
  number: number;
  onNumber: (n: number) => void;
  mode: OverlayMode;
  zoom: number; // 1 = fit the pane's width
  selected: number | null;
  onSelect: (i: number | null) => void;
  onPage?: (page: number) => void;  // a click on a page: the editing panel shows that page
  ops: TagEdit[];
  scrollRef: (el: HTMLDivElement | null) => void;
  onScroll: () => void;
  onLayout: (l: PaneLayout) => void;
  onData?: (d: PaneData | null) => void;
  highlight?: number | null; // a tag hovered in the tree
  onTree: () => void;
  treeDisabled: boolean;
  tools?: ReactNode; // viewing controls shown at the end of this pane's header row
  onlyPage?: number | null; // one-page view: show just this page (1-based)
  areaMode?: boolean;
  area?: Area | null;
  onArea?: (page: number, bbox: [number, number, number, number]) => void;
  picked?: number[];
  onPick?: (i: number) => void;
  onMenu?: (i: number, x: number, y: number, at: { page: number; x: number; y: number } | null) => void;
  onPageMenu?: (page: number, x: number, y: number) => void;
  isolated?: number | null; // a tag isolated: the pages are veiled except for it and what's inside it
  spot?: Spot | null; // a review item's area, highlighted on its page (#426)
  onSpot?: (f: Finding) => void;
  onSpotClose?: () => void;
  pendingSel?: number | null; // a tag pending edits make, chosen (#427): the place of the edit making it
  onPendingSelect?: (op: number) => void;
};

export const TREE = "tree";

/** The version picker at the top of a pane, with the tag-tree view of the other pane as a last choice. */
export function VersionPicker({ side, versions, value, onNumber, onTree, treeDisabled }: {
  side: "left" | "right"; versions: DocVersion[]; value: number | typeof TREE;
  onNumber: (n: number) => void; onTree: () => void; treeDisabled: boolean;
}) {
  return (
    <>
      <label htmlFor={`pick-${side}`} className="visually-hidden">{side === "left" ? "Left" : "Right"} pane view</label>
      <select id={`pick-${side}`} value={value}
        onChange={(e) => (e.target.value === TREE ? onTree() : onNumber(Number(e.target.value)))}>
        {versions.map((v) => (
          <option key={v.number} value={v.number} disabled={v.status !== "ready" || !v.has_tagmap}>
            {v.label}{versionStatus(v) ? ` — ${versionStatus(v)}` : ""}
          </option>
        ))}
        <option value={TREE} disabled={treeDisabled}>
          Tag tree of the {side === "left" ? "right" : "left"}-hand version
        </option>
      </select>
    </>
  );
}

/** A version's loading in words, and how far its downloads have got (null once they're done, or not yet known). */
// pdfSize: the file's size as the version records it, for when the download doesn't say
export function loadingWords(load: LoadState | null,
  pdfSize?: number | null): { lines: string[]; fraction: number | null } {
  if (load?.reading) return { lines: [READING_TAGS], fraction: null };
  const none = { received: 0, total: null };
  const pdf = load?.pdf ?? none;
  const doc = { ...pdf, total: pdf.total ?? (pdfSize || null) };
  return {
    lines: [downloading("the document", doc), downloading("the tags", load?.tags ?? none)],
    fraction: load ? fraction([doc, load.tags]) : null,
  };
}

export function versionStatus(v: DocVersion): string {
  if (v.status === "failed") return "failed";
  if (v.status !== "ready") return "saving…";
  if (!v.has_tagmap) return "not in the tag manager";
  if (v.checking) return "checking…";
  if (v.summary?.checks_unfinished) return "checks didn't finish";
  const s = v.summary;
  if (!s || s.compliant === null) return "";
  return s.compliant ? "passes" : `${fmtNum(s.failed_checks)} failed checks`;
}

export function Pane({ side, jobId, versions, number, onNumber, mode, zoom, selected, onSelect, onPage, ops, scrollRef, onScroll, onLayout, onData, highlight = null, onTree, treeDisabled,
  areaMode = false, area = null, onArea, onlyPage = null, tools, picked, onPick, onMenu, onPageMenu, isolated = null,
  spot = null, onSpot, onSpotClose, pendingSel = null, onPendingSelect }: Props) {
  const version = versions.find((v) => v.number === number);
  const [data, setData] = useState<PaneData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [width, setWidth] = useState(600);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  // While it loads: how far the downloads have got, then whether a page has been drawn yet (#424)
  const [load, setLoad] = useState<LoadState | null>(null);
  const [drawn, setDrawn] = useState(false);
  const markDrawn = useCallback(() => setDrawn(true), []);

  useEffect(() => {
    setData(null);
    setError(null);
    setLoad(null);
    setDrawn(false);
    onData?.(null);
    if (!version || version.status !== "ready" || !version.has_tagmap) return;
    let live = true;
    loadVersion(jobId, number).then(
      (d) => { if (live) { setData(d); onData?.(d); } },
      (e) => { if (live) setError(e); },
    );
    const unwatch = watchVersion(jobId, number, setLoad);
    return () => { live = false; unwatch(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, number, version?.status]);

  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width - 24)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pages = data?.tagmap.pages ?? [];
  const maxW = Math.max(1, ...pages.map((p) => p.w));
  const scale = (width / maxW) * zoom;

  useEffect(() => {
    const offsets: number[] = [];
    const heights: number[] = [];
    let y = PAGE_TOP;
    for (const p of pages) {
      offsets.push(y);
      heights.push(p.h * scale);
      y += p.h * scale + PAGE_GAP;
    }
    onLayout({ offsets, heights, scale, only: onlyPage && pages.length ? Math.min(onlyPage, pages.length) : null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages, scale, onlyPage]);

  // A new page in one-page view starts at its top
  useEffect(() => { if (onlyPage && bodyRef.current) bodyRef.current.scrollTop = 0; }, [onlyPage]);
  const shownPage = onlyPage && pages.length ? Math.min(onlyPage, pages.length) : null;

  const findings = version?.summary?.findings ?? [];
  const over = useMemo(() => (side === "right" ? overrides(ops, data?.tagmap) : new Map()), [ops, side, data]);
  const merged = useMemo(() => (side === "right" ? absorbedBy(ops) : new Map<number, number>()), [ops, side]);
  // A click on the page means the innermost tag under the pointer; the same place again, the next one out
  const lastHit = useRef<LastHit>(null);
  const hitAt = (page: number, x: number, y: number, menu = false): number | null => {
    if (!data) return null;
    const skip = new Set([...merged.keys(), ...(side === "right" ? deletedTags(data.tagmap, ops) : [])]);
    const here = tagsAt(data.tagmap, page, x, y, skip);
    if (menu) return selected !== null && here.includes(selected) ? selected : here[0] ?? null;
    const chose = chooseAt(here, selected, lastHit.current);
    lastHit.current = chose === null ? null : { first: here[0], chose };
    return chose;
  };
  const inside = useMemo(() => (isolated !== null && data ? subtree(data.tagmap, isolated) : null), [isolated, data]);

  // Overlay boxes per page
  const perPage = useMemo(() => {
    const out: { items: OverlayItem[]; notes: Finding[] }[] = pages.map(() => ({ items: [], notes: [] }));
    if (!data) return out;
    const byEl = new Map<number, typeof findings>();
    for (const f of findings) {
      if (f.element == null || !data.tagmap.elements[f.element]) {
        if (f.page && out[f.page - 1]) out[f.page - 1].notes.push(f);
        continue;
      }
      byEl.set(f.element, [...(byEl.get(f.element) ?? []), f]);
    }
    const orderNo = new Map<number, number>();
    if (mode === "order") {
      pages.forEach((_, p) => pageOrder(data.tagmap, p + 1, side === "right" ? ops : []).forEach((e, n) => orderNo.set(e.i, n + 1)));
    }
    const deleted = side === "right" ? deletedTags(data.tagmap, ops) : new Set<number>();
    // A list being built from a paragraph is drawn as it will be saved: the heading, each list and each item, in
    // place of the paragraph (and of the tags it takes in)
    const building = side === "right" ? ops.filter((o): o is Extract<TagEdit, { op: "build_list" | "build_table" | "rebuild_table" }> =>
      o.op === "build_list" || o.op === "build_table" || o.op === "rebuild_table") : [];
    // ...and a table made or edited (#370), in place of the tags it replaces
    const builtFrom = new Set(building.flatMap((o) => [o.element, ...(o.op === "build_list" ? o.absorb ?? [] : tableReplaced(data.tagmap, o))]));
    for (const op of building) {
      const el = data.tagmap.elements[op.element];
      if (!el || (mode === "off" && el.i !== selected)) continue;
      for (const row of op.op === "build_list" ? builtPreview(data.tagmap, op) : tablePreview(data.tagmap, op)) {
        // A list's (or table's, or row's) outline is set a little outside what it holds, or it would lie on their
        // edges and not be seen
        const pad = row.role === "Table" ? 4 : row.role === "TR" ? 2 : row.role !== "L" ? 0 : row.depth === 0 ? 4 : 2;
        for (const b of row.boxes) {
          const [x0, y0, x1, y1] = b.bbox;
          out[b.page - 1]?.items.push({ el, bbox: [x0 - pad, y0 - pad, x1 + pad, y1 + pad], role: row.role, findings: [], text: row.text, pending: true });
        }
      }
    }
    // A figure being split: each part it will make, as it will be once saved
    if (side === "right") {
      for (const [from, parts] of splitPreview(data.tagmap, ops)) {
        const el = data.tagmap.elements[from];
        if (!el || (mode === "off" && el.i !== selected)) continue;
        for (const part of parts) {
          for (const b of part.boxes) out[b.page - 1]?.items.push({ el, bbox: b.bbox, role: part.role, findings: [], text: part.alt, pending: true });
        }
      }
    }
    // Tags pending edits make (an area of untagged content, a split) and content they move between tags (#427):
    // drawn at once, whatever is being shown, as they will be once saved
    const moves = side === "right" ? contentMoves(data.tagmap, ops) : null;
    const keeps = side === "right" ? splitKeeps(data.tagmap, ops) : new Map<number, Box[]>();
    if (side === "right") {
      for (const t of newTags(data.tagmap, ops)) {
        const el = data.tagmap.elements[t.anchor];
        if (!el) continue;
        for (const b of t.boxes) {
          out[b.page - 1]?.items.push({ el, bbox: b.bbox, role: t.role, findings: [], text: t.alt || t.text,
            pending: true, made: t.op, madeSelected: t.op === pendingSel });
        }
      }
      for (const [i, boxes] of moves!.added) {
        const el = data.tagmap.elements[i];
        if (!el) continue;
        const role = over.get(i)?.role ?? el.role;
        for (const b of boxes) {
          out[b.page - 1]?.items.push({ el, bbox: b.bbox, role, findings: [], text: el.text, pending: true });
        }
      }
    }
    for (const raw of data.tagmap.elements) {
      if (deleted.has(raw.i) || builtFrom.has(raw.i) || moves?.emptied.has(raw.i)) continue;
      // A tag merged into another by a pending edit is drawn as part of the tag that absorbs it
      const el = merged.has(raw.i) ? data.tagmap.elements[merged.get(raw.i)!] : raw;
      const fs = byEl.get(el.i) ?? [];
      const show = el.i === selected || !!picked?.includes(el.i) || el.i === highlight || (mode === "flagged" && fs.length > 0) || ((mode === "all" || mode === "order") && raw.own);
      if (!show) continue;
      const o = over.get(el.i) ?? {};
      for (const b of moves?.boxes.get(raw.i) ?? keeps.get(raw.i) ?? raw.boxes) {
        out[b.page - 1]?.items.push({
          el, bbox: b.bbox, role: o.role ?? el.role, findings: fs, decorative: o.decorative, alt: o.alt, lang: o.lang,
          orderNo: mode === "order" && raw === el ? orderNo.get(el.i) : undefined, highlighted: el.i === highlight,
        });
      }
    }
    return out;
  }, [data, pages, findings, mode, selected, picked, highlight, over, merged, ops, side, pendingSel]);

  const picker = (
    <div className="pv-head">
      <VersionPicker side={side} versions={versions} value={number} onNumber={onNumber} onTree={onTree}
        treeDisabled={treeDisabled} />
      {side === "left" && <span className="pill muted">Read only</span>}
      {version?.summary && version.kind !== "original" && (
        <span className="small muted">{fmtNum(version.summary.findings_total)} to review</span>
      )}
      {version?.summary?.compliant === false && checkLink(jobId, version) && (
        // Which checks failed: the version's own results on the document page
        <Link className="small" to={checkLink(jobId, version)!}>See the {fmtNum(version.summary.failed_checks)} failed check{version.summary.failed_checks === 1 ? "" : "s"}</Link>
      )}
      {tools}
    </div>
  );

  // The wait, in words (#424): the two downloads, then reading them, then drawing the first page in view. The pages
  // say they are busy until then; the progress sits outside them, as a busy region's announcements may be held back
  const drawing = !!data && !drawn && pages.length > 0;
  const progress = data && !drawing ? null : (
    <LoadProgress className={data ? "pv-loading pv-loading-over" : "pv-loading"}
      {...(data ? { lines: [drawingPage(shownPage ?? 1)], fraction: null } : loadingWords(load, version?.pdf_size))}
      note={isLarge({ pages: data?.tagmap.pages.length ?? load?.pages, bytes: version?.pdf_size ?? load?.pdf.total })
        ? LARGE_NOTE : null} />
  );

  return (
    <section className="pv-pane" aria-label={`${side === "left" ? "Left" : "Right"}: ${version?.label ?? ""}`}>
      {picker}
      {/* Focusable, so the pages can be scrolled from the keyboard before any tag on them is chosen (#369) */}
      <div className="pv-body" ref={(el) => { bodyRef.current = el; scrollRef(el); }} onScroll={onScroll} tabIndex={0}
        role="region" aria-label={`Pages of ${version?.label ?? "this version"}`}>
        {!version || version.status !== "ready" ? (
          <p className="muted pv-msg">This version isn't ready.</p>
        ) : !version.has_tagmap ? (
          <p className="muted pv-msg">This version can't be opened in the tag manager.</p>
        ) : error ? (
          <p className="pv-msg" role="alert">{errorMessage(error)}</p>
        ) : !data ? (
          <>{progress}<div className="pv-pages" aria-busy="true" /></>
        ) : (
          <>{progress}
          <div className={`pv-pages${zoom > 1 ? " zoomed" : ""}`} aria-busy={drawing || undefined}>
            {pages.map((p, i) => shownPage !== null && i !== shownPage - 1 ? null : (
              <PageView key={i} pdf={data.pdf} index={i} size={p} scale={scale} items={perPage[i]?.items ?? []}
                pageFindings={perPage[i]?.notes ?? []} showLabels={mode === "all"} selected={selected}
                onSelect={(el) => onSelect(el === selected ? null : el)} onPage={onPage} hitAt={hitAt}
                areaMode={areaMode} area={area?.page === i + 1 ? area.bbox : null} onArea={onArea}
                picked={picked} onPick={onPick} onMenu={onMenu} onPageMenu={onPageMenu}
                veil={inside && data ? holes(data.tagmap, inside, i + 1) : null} onDrawn={markDrawn}
                spot={spot?.page === i + 1 ? spot : null} onSpot={onSpot} onSpotClose={onSpotClose}
                onPendingSelect={onPendingSelect} />
            ))}
          </div>
          </>
        )}
      </div>
    </section>
  );
}
