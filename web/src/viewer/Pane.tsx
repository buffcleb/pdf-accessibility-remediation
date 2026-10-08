import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DocVersion, Finding, TagEdit } from "../api";
import { errorMessage, fmtNum } from "../components/ui";
import { absorbedBy, overrides, pageOrder } from "./draft";
import { loadVersion, type PaneData } from "./pdf";
import { PageView, type OverlayItem } from "./PageView";

export type OverlayMode = "flagged" | "all" | "order" | "off";
export type PaneLayout = { offsets: number[]; heights: number[]; scale: number };

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
  ops: TagEdit[];
  scrollRef: (el: HTMLDivElement | null) => void;
  onScroll: () => void;
  onLayout: (l: PaneLayout) => void;
  onData?: (d: PaneData | null) => void;
  highlight?: number | null; // a tag hovered in the tree
  onTree: () => void;
  treeDisabled: boolean;
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

export function versionStatus(v: DocVersion): string {
  if (v.status === "failed") return "failed";
  if (v.status !== "ready") return "saving…";
  if (!v.has_tagmap) return "no tag view";
  const s = v.summary;
  if (!s || s.compliant === null) return "";
  return s.compliant ? "passes" : `${fmtNum(s.failed_checks)} failed checks`;
}

export function Pane({ side, jobId, versions, number, onNumber, mode, zoom, selected, onSelect, ops, scrollRef, onScroll, onLayout, onData, highlight = null, onTree, treeDisabled }: Props) {
  const version = versions.find((v) => v.number === number);
  const [data, setData] = useState<PaneData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [width, setWidth] = useState(600);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    onData?.(null);
    if (!version || version.status !== "ready" || !version.has_tagmap) return;
    let live = true;
    loadVersion(jobId, number).then(
      (d) => { if (live) { setData(d); onData?.(d); } },
      (e) => { if (live) setError(e); },
    );
    return () => { live = false; };
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
    let y = 12;
    for (const p of pages) {
      offsets.push(y);
      heights.push(p.h * scale);
      y += p.h * scale + PAGE_GAP;
    }
    onLayout({ offsets, heights, scale });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages, scale]);

  const findings = version?.summary?.findings ?? [];
  const over = useMemo(() => (side === "right" ? overrides(ops) : new Map()), [ops, side]);
  const merged = useMemo(() => (side === "right" ? absorbedBy(ops) : new Map<number, number>()), [ops, side]);

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
    for (const raw of data.tagmap.elements) {
      // A tag merged into another by a pending edit is drawn as part of the tag that absorbs it
      const el = merged.has(raw.i) ? data.tagmap.elements[merged.get(raw.i)!] : raw;
      const fs = byEl.get(el.i) ?? [];
      const show = el.i === selected || el.i === highlight || (mode === "flagged" && fs.length > 0) || ((mode === "all" || mode === "order") && raw.own);
      if (!show) continue;
      const o = over.get(el.i) ?? {};
      for (const b of raw.boxes) {
        out[b.page - 1]?.items.push({
          el, bbox: b.bbox, role: o.role ?? el.role, findings: fs, decorative: o.decorative, alt: o.alt, lang: o.lang,
          orderNo: mode === "order" && raw === el ? orderNo.get(el.i) : undefined, highlighted: el.i === highlight,
        });
      }
    }
    return out;
  }, [data, pages, findings, mode, selected, highlight, over, merged, ops, side]);

  const picker = (
    <div className="pv-head">
      <VersionPicker side={side} versions={versions} value={number} onNumber={onNumber} onTree={onTree}
        treeDisabled={treeDisabled} />
      {side === "left" && <span className="pill muted">Read only</span>}
      {version?.summary && version.kind !== "original" && (
        <span className="small muted">{fmtNum(version.summary.findings_total)} to review</span>
      )}
    </div>
  );

  return (
    <section className="pv-pane" aria-label={`${side === "left" ? "Left" : "Right"}: ${version?.label ?? ""}`}>
      {picker}
      <div className="pv-body" ref={(el) => { bodyRef.current = el; scrollRef(el); }} onScroll={onScroll}>
        {!version || version.status !== "ready" ? (
          <p className="muted pv-msg">This version isn't ready.</p>
        ) : !version.has_tagmap ? (
          <p className="muted pv-msg">Tag view isn't available for this version.</p>
        ) : error ? (
          <p className="pv-msg" role="alert">{errorMessage(error)}</p>
        ) : !data ? (
          <p className="muted pv-msg" role="status">Loading pages…</p>
        ) : (
          <div className={`pv-pages${zoom > 1 ? " zoomed" : ""}`}>
            {pages.map((p, i) => (
              <PageView key={i} pdf={data.pdf} index={i} size={p} scale={scale} items={perPage[i]?.items ?? []}
                pageFindings={perPage[i]?.notes ?? []} showLabels={mode === "all"} selected={selected}
                onSelect={(el) => onSelect(el === selected ? null : el)} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
