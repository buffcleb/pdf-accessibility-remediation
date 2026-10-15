import { memo, useEffect, useRef, useState } from "react";
import type { PDFDoc } from "./pdf";
import { ROLE_NAMES, roleClass } from "./draft";
import type { Finding, TagElement } from "../api";

export type OverlayItem = {
  el: TagElement;
  bbox: [number, number, number, number];
  role: string; // after pending edits
  findings: (Finding & { extra?: { suggested_alt?: string } })[];
  orderNo?: number;
  decorative?: boolean;
  alt?: string;
  lang?: string;
  highlighted?: boolean;
  text?: string;     // its own words, when they aren't the tag's (a row of a list built from it)
  pending?: boolean; // part of an unsaved change: drawn as it will be once saved
};

const SEV_RANK: Record<string, number> = { high: 0, review: 1, suggest: 2 };

export function worstSeverity(findings: Finding[]): string | null {
  let best: string | null = null;
  for (const f of findings) if (best === null || (SEV_RANK[f.severity] ?? 9) < (SEV_RANK[best] ?? 9)) best = f.severity;
  return best;
}

type Props = {
  pdf: PDFDoc | null;
  index: number; // 0-based
  size: { w: number; h: number };
  scale: number;
  items: OverlayItem[];
  pageFindings: Finding[]; // findings on this page that aren't tied to a drawn element
  showLabels: boolean;
  selected: number | null;
  onSelect: (i: number) => void;
  onPage?: (page: number) => void;
  areaMode?: boolean; // dragging on the page draws an area instead of selecting tags
  area?: [number, number, number, number] | null; // the chosen area on this page, in page units
  onArea?: (page: number, bbox: [number, number, number, number]) => void;
  picked?: number[]; // other tags selected along with `selected`
  onPick?: (i: number) => void; // Ctrl or Cmd with a click: add the tag to the selection, or take it out
  // A right-click (or Shift+F10) on a tag: where on the screen, and where on the page when a pointer asked
  onMenu?: (i: number, x: number, y: number, at: { page: number; x: number; y: number } | null) => void;
  onPageMenu?: (page: number, x: number, y: number) => void; // a right-click on the page where there is no tag
  // The tag a click at a point (page units) means: the innermost there, or the next out on a repeat. For the menu
  // (`menu`), the selected tag when it is there, so a right-click acts on what was chosen
  hitAt?: (page: number, x: number, y: number, menu?: boolean) => number | null;
  veil?: [number, number, number, number][] | null; // a tag is isolated: the page is veiled except these boxes
};

/** One page: the rendered canvas (only while near the viewport) with tag boxes drawn over it. */
export const PageView = memo(function PageView({ pdf, index, size, scale, items, pageFindings, showLabels, selected, onSelect, onPage,
  areaMode = false, area = null, onArea, picked, onPick, onMenu, onPageMenu, hitAt, veil = null }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  // A pointer event's place on this page, in page units
  const at = (e: { clientX: number; clientY: number }) => {
    const r = wrapRef.current?.getBoundingClientRect();
    return r ? { x: (e.clientX - r.left) / scale, y: (e.clientY - r.top) / scale } : null;
  };
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  // Clicking blank page hides the details box: people usually want to see what's behind it
  const [popHidden, setPopHidden] = useState(false);
  useEffect(() => setPopHidden(false), [selected]);
  const w = size.w * scale;
  const h = size.h * scale;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), { rootMargin: "150% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!near || !pdf || !canvasRef.current) return;
    let canceled = false;
    let task: { cancel: () => void; promise: Promise<void> } | null = null;
    (async () => {
      const page = await pdf.getPage(index + 1);
      if (canceled || !canvasRef.current) return;
      const base = page.getViewport({ scale: 1 });
      // Sharp on high-DPI screens, but capped so a zoomed page doesn't need an enormous canvas
      const dpr = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(12e6 / Math.max(1, w * h)));
      const viewport = page.getViewport({ scale: (w / base.width) * dpr });
      const canvas = canvasRef.current;
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      task = page.render({ canvas, viewport });
      await task.promise.catch(() => {});
    })();
    return () => {
      canceled = true;
      task?.cancel();
    };
  }, [near, pdf, index, w]);

  const shown = items.find((it) => it.el.i === hover) ?? (popHidden ? undefined
    : items.find((it) => it.highlighted && hover === null) ?? items.find((it) => it.el.i === selected && hover === null));
  return (
    <div className="pv-page" ref={wrapRef} style={{ width: w, height: h }} data-page={index + 1}
      onMouseLeave={() => setHover(null)}
      onContextMenu={(e) => {
        // On a tag the tag's own menu has opened (and stopped this); anywhere else on the page, the page's
        if (!onPageMenu || e.defaultPrevented) return;
        e.preventDefault();
        onPageMenu(index + 1, e.clientX, e.clientY);
      }}
      onClick={(e) => {
        onPage?.(index + 1);
        if (!(e.target as HTMLElement).closest(".pv-box")) { setHover(null); setPopHidden(true); }
      }}>
      {near ? <canvas ref={canvasRef} className="pv-canvas" style={{ width: w, height: h }} aria-hidden="true" /> : null}
      <span className="pv-pageno" aria-hidden="true">{index + 1}</span>
      {pageFindings.length > 0 && (
        <div className="pv-pagenote" title={pageFindings.map((f) => f.issue).join("; ")}>
          {pageFindings.length} page-level note{pageFindings.length > 1 ? "s" : ""}
        </div>
      )}
      {items.map((it) => {
        const [x0, y0, x1, y1] = it.bbox;
        const sev = worstSeverity(it.findings);
        const name = ROLE_NAMES[it.role] ?? it.role;
        const words = it.text ?? it.el.text ?? it.el.name;
        const label = `${name}${it.pending ? " (not saved)" : ""}${it.findings.length ? `: ${it.findings[0].issue}` : ""}${words ? ` — ${words.slice(0, 60)}` : ""}`;
        return (
          <button key={`${it.el.i}-${it.role}-${x0}-${y0}`} type="button"
            className={`pv-box ${roleClass(it.role)}${it.pending ? " pending" : ""}${sev ? ` sev-${sev}` : ""}${selected === it.el.i || picked?.includes(it.el.i) ? " selected" : ""}${it.decorative ? " decorative" : ""}${it.highlighted ? " hl" : ""}`}
            style={{ left: x0 * scale, top: y0 * scale, width: Math.max(4, (x1 - x0) * scale), height: Math.max(4, (y1 - y0) * scale) }}
            tabIndex={sev || selected === it.el.i ? 0 : -1}
            aria-label={label}
            onMouseEnter={() => { setHover(it.el.i); setPopHidden(false); }}
            onFocus={() => setHover(it.el.i)}
            onBlur={() => setHover(null)}
            onClick={(e) => {
              const p = at(e);
              const i = (p && hitAt?.(index + 1, p.x, p.y)) ?? it.el.i;
              if ((e.ctrlKey || e.metaKey) && onPick) onPick(i);
              else onSelect(i);
            }}
            onContextMenu={(e) => {
              if (!onMenu) return;
              e.preventDefault();
              const p = at(e);
              onMenu((p && hitAt?.(index + 1, p.x, p.y, true)) ?? it.el.i, e.clientX, e.clientY, p ? { page: index + 1, ...p } : null);
            }}
            onKeyDown={(e) => {
              if (!onMenu || !(e.key === "ContextMenu" || (e.shiftKey && e.key === "F10"))) return;
              e.preventDefault();
              const box = e.currentTarget.getBoundingClientRect();
              onMenu(it.el.i, box.left + 12, box.bottom, null);
            }}>
            {(showLabels || it.orderNo !== undefined) && (
              <span className="pv-chip">{it.orderNo !== undefined ? `${it.orderNo}` : it.role}</span>
            )}
          </button>
        );
      })}
      {shown && !areaMode && <Popover item={shown} scale={scale} pageH={h} pageW={w} />}
      {veil && (
        // Everything but the isolated tag is veiled. It lets clicks through: the rest can still be chosen
        <svg className="pv-veil" aria-hidden="true" width={w} height={h}>
          <defs>
            <mask id={`pv-veil-${index}`}>
              <rect width={w} height={h} fill="white" />
              {veil.map((b, n) => <rect key={n} x={b[0] * scale - 2} y={b[1] * scale - 2} width={(b[2] - b[0]) * scale + 4} height={(b[3] - b[1]) * scale + 4} fill="black" />)}
            </mask>
          </defs>
          <rect className="pv-veil-fill" width={w} height={h} mask={`url(#pv-veil-${index})`} />
        </svg>
      )}
      {area && <div className="pv-area" aria-hidden="true" style={{ left: area[0] * scale, top: area[1] * scale,
        width: (area[2] - area[0]) * scale, height: (area[3] - area[1]) * scale }} />}
      {areaMode && onArea && <AreaDraw scale={scale} size={size} onArea={(b) => onArea(index + 1, b)} />}
    </div>
  );
});

function Popover({ item, scale, pageH, pageW }: { item: OverlayItem; scale: number; pageH: number; pageW: number }) {
  const [x0, y0, , y1] = item.bbox;
  const below = y1 * scale + 8 + 160 < pageH;
  const style = {
    left: Math.min(Math.max(0, x0 * scale), Math.max(0, pageW - 340)),
    ...(below ? { top: y1 * scale + 6 } : { bottom: pageH - y0 * scale + 6 }),
  };
  return (
    <div className="pv-pop" style={style} role="tooltip">
      <div className="pv-pop-head">
        <strong>{ROLE_NAMES[item.role] ?? item.role}</strong> <span className="mono muted">{item.role}</span>
        {item.decorative && <span className="pill muted">decorative (pending)</span>}
        {item.pending && <span className="pill muted">not saved</span>}
      </div>
      {item.el.actual_text && !item.el.alt ? (
        <>
          <p className="small"><span className="muted">Read aloud as:</span> “{item.el.actual_text.slice(0, 160)}{item.el.actual_text.length > 160 ? "…" : ""}”</p>
          {item.el.text && <p className="small muted">Drawn text: “{item.el.text.slice(0, 100)}{item.el.text.length > 100 ? "…" : ""}”</p>}
        </>
      ) : item.text !== undefined ? (item.text && <p className="small">“{item.text.slice(0, 160)}{item.text.length > 160 ? "…" : ""}”</p>)
        : item.el.text && <p className="small">“{item.el.text.slice(0, 160)}{item.el.text.length > 160 ? "…" : ""}”</p>}
      {item.el.name && !item.el.text && <p className="small"><span className="muted">Announced as:</span> “{item.el.name.slice(0, 160)}”</p>}
      {(item.alt ?? item.el.alt) && <p className="small"><strong>Alt:</strong> {item.alt ?? item.el.alt}</p>}
      {(item.lang ?? item.el.lang) && <p className="small"><strong>Language:</strong> {item.lang ?? item.el.lang}</p>}
      {item.el.scope && <p className="small"><strong>Scope:</strong> {item.el.scope}</p>}
      {item.findings.map((f, n) => (
        <div key={n} className={`pv-finding sev-${f.severity}`}>
          <div><span className={`pill ${f.severity === "high" ? "bad" : f.severity === "review" ? "warn" : "info"}`}>{f.severity}</span> {f.issue}</div>
          {f.detail && <div className="small">{f.detail}</div>}
          {f.action && <div className="small muted">{f.action}</div>}
          {f.confidence != null && <div className="small muted">Confidence {Math.round(f.confidence * 100)}%</div>}
        </div>
      ))}
    </div>
  );
}

/** A layer over the page while choosing an area: drag a rectangle (the editing panel has a keyboard alternative). */
function AreaDraw({ scale, size, onArea }: { scale: number; size: { w: number; h: number };
  onArea: (b: [number, number, number, number]) => void }) {
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const at = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: Math.min(size.w, Math.max(0, (e.clientX - r.left) / scale)), y: Math.min(size.h, Math.max(0, (e.clientY - r.top) / scale)) };
  };
  return (
    <div className="pv-area-draw" aria-hidden="true"
      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); const p = at(e); setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }); }}
      onPointerMove={(e) => { if (drag) { const p = at(e); setDrag({ ...drag, x1: p.x, y1: p.y }); } }}
      onPointerUp={() => {
        if (drag && Math.abs(drag.x1 - drag.x0) * scale > 4 && Math.abs(drag.y1 - drag.y0) * scale > 4) {
          onArea([Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)]);
        }
        setDrag(null);
      }}
      onClick={(e) => e.stopPropagation()}>
      {drag && <div className="pv-area drawing" style={{ left: Math.min(drag.x0, drag.x1) * scale, top: Math.min(drag.y0, drag.y1) * scale,
        width: Math.abs(drag.x1 - drag.x0) * scale, height: Math.abs(drag.y1 - drag.y0) * scale }} />}
    </div>
  );
}
