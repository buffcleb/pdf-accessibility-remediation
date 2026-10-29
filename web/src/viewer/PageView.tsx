import { Fragment, memo, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { PDFDoc } from "./pdf";
import { ROLE_NAMES, roleClass } from "./draft";
import { CARD_W, cardWidth, placeCard, tagText, type CardPlace } from "./fullText";
import type { Finding, TagElement } from "../api";
import { PageNotes, SpotLayer } from "./SpotLayer";
import type { Spot } from "./spot";

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
  cards?: boolean; // a tag's card shows on hover and focus; off, the person turned them off for now
  selected: number | null;
  onSelect: (i: number) => void;
  onPage?: (page: number) => void;
  areaMode?: boolean; // dragging on the page draws an area instead of selecting tags
  area?: [number, number, number, number] | null; // the chosen area on this page, in page units
  boxes?: { bbox: [number, number, number, number]; label: string }[]; // the AI's figures for the area, numbered
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
  spot?: Spot | null; // a review item's area on this page, highlighted (#426)
  onSpot?: (f: Finding) => void; // one of the page's own items chosen in its list
  onSpotClose?: () => void;
  onDrawn?: () => void; // the page has been drawn (or couldn't be): the pane stops showing its progress (#424)
};

/** One page: the rendered canvas (only while near the viewport) with tag boxes drawn over it. */
export const PageView = memo(function PageView({ pdf, index, size, scale, items, pageFindings, showLabels, cards = true, selected, onSelect, onPage,
  areaMode = false, area = null, boxes = [], onArea, picked, onPick, onMenu, onPageMenu, hitAt, veil = null,
  onDrawn, spot = null, onSpot, onSpotClose }: Props) {
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
      try {
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
      } catch {
        // a page pdf.js can't read stays blank
      } finally {
        if (!canceled) onDrawn?.();  // drawn or not, the wait is over: the progress mustn't stay up
      }
    })();
    return () => {
      canceled = true;
      task?.cancel();
    };
  }, [near, pdf, index, w]);

  // A tag hovered in the tree is only outlined here: its card would cover the page being looked at beside the tree
  const shown = items.find((it) => it.el.i === hover) ?? (popHidden ? undefined
    : items.find((it) => it.el.i === selected && hover === null));
  const popId = useId();
  const card = shown && !areaMode && cards ? shown : undefined;
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
      {pageFindings.length > 0 && <PageNotes notes={pageFindings} onShow={onSpot} />}
      {items.map((it) => {
        const [x0, y0, x1, y1] = it.bbox;
        const sev = worstSeverity(it.findings);
        const name = ROLE_NAMES[it.role] ?? it.role;
        const words = it.text ?? it.el.text ?? it.el.name;
        // The chip drawn on it (its type, or its place in the reading order) starts its name: what's seen is what's said
        const chip = showLabels || it.orderNo !== undefined ? (it.orderNo !== undefined ? `${it.orderNo}` : it.role) : null;
        const label = `${chip !== null ? `${chip}, ` : ""}${name}${it.pending ? " (not saved)" : ""}`
          + `${it.findings.length ? `: ${it.findings[0].issue}` : ""}${words ? ` — ${words.slice(0, 60)}` : ""}`;
        return (
          <Fragment key={`${it.el.i}-${it.role}-${x0}-${y0}`}>
          <button type="button"
            className={`pv-box ${roleClass(it.role)}${it.pending ? " pending" : ""}${sev ? ` sev-${sev}` : ""}${selected === it.el.i || picked?.includes(it.el.i) ? " selected" : ""}${it.decorative ? " decorative" : ""}${it.highlighted ? " hl" : ""}`}
            style={{ left: x0 * scale, top: y0 * scale, width: Math.max(4, (x1 - x0) * scale), height: Math.max(4, (y1 - y0) * scale) }}
            tabIndex={sev || selected === it.el.i ? 0 : -1}
            aria-label={label} aria-describedby={it === card ? popId : undefined}
            onMouseEnter={() => { setHover(it.el.i); setPopHidden(false); }}
            onFocus={() => setHover(it.el.i)}
            // Tabbing on into the card (when it scrolls) keeps it open
            onBlur={(e) => { if (!(e.relatedTarget as Element | null)?.closest?.(".pv-pop")) setHover(null); }}
            onClick={(e) => {
              const p = at(e);
              // A tag the changes make is chosen by its own outline: the tag it came from can lie under it, line for line
              const i = it.el.made ? it.el.i : (p && hitAt?.(index + 1, p.x, p.y)) ?? it.el.i;
              if ((e.ctrlKey || e.metaKey) && onPick) onPick(i);
              else onSelect(i);
            }}
            onContextMenu={(e) => {
              if (!onMenu) return;
              e.preventDefault();
              const p = at(e);
              onMenu(it.el.made ? it.el.i : (p && hitAt?.(index + 1, p.x, p.y, true)) ?? it.el.i, e.clientX, e.clientY,
                p ? { page: index + 1, ...p } : null);
            }}
            onKeyDown={(e) => {
              if (!onMenu || !(e.key === "ContextMenu" || (e.shiftKey && e.key === "F10"))) return;
              e.preventDefault();
              const box = e.currentTarget.getBoundingClientRect();
              onMenu(it.el.i, box.left + 12, box.bottom, null);
            }}>
            {chip !== null && <span className="pv-chip">{chip}</span>}
          </button>
          {/* Straight after its tag, so the Tab key goes on into it when it scrolls */}
          {it === card && <Popover id={popId} item={it} scale={scale} onLeave={() => setHover(null)} />}
          </Fragment>
        );
      })}
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
      {spot && onSpotClose && <SpotLayer spot={spot} index={index} scale={scale} w={w} h={h} onClose={onSpotClose} />}
      {boxes.map((b, k) => (
        <div key={k} className="pv-area pv-area-figure" aria-hidden="true" style={{ left: b.bbox[0] * scale, top: b.bbox[1] * scale,
          width: (b.bbox[2] - b.bbox[0]) * scale, height: (b.bbox[3] - b.bbox[1]) * scale }}><span>{b.label}</span></div>
      ))}
      {area && <div className="pv-area" aria-hidden="true" style={{ left: area[0] * scale, top: area[1] * scale,
        width: (area[2] - area[0]) * scale, height: (area[3] - area[1]) * scale }} />}
      {areaMode && onArea && <AreaDraw scale={scale} size={size} onArea={(b) => onArea(index + 1, b)} />}
    </div>
  );
});

/** The card with a tag's details, in full (#436). It's placed by placeCard once drawn, as it has to be measured: inside
 * the part of the pane that can be seen, and placed again as the pane scrolls. Too tall, it scrolls: then it takes the
 * pointer and the Tab key, so it can be scrolled. */
function Popover({ id, item, scale, onLeave }: { id: string; item: OverlayItem; scale: number; onLeave: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<(CardPlace & { scrolls: boolean }) | null>(null);
  const [x0, y0, x1, y1] = item.bbox;
  const words = tagText(item.el);
  useLayoutEffect(() => {
    const card = ref.current;
    const page = card?.parentElement;
    if (!card || !page) return;
    const body = page.closest<HTMLElement>(".pv-body");
    const fit = () => {
      const p = page.getBoundingClientRect();
      // What can be seen of the pane (inside its scroll bars) and of the window, in the page's pixels
      const b = body ? body.getBoundingClientRect() : p;
      const l = b.left + (body?.clientLeft ?? 0), t = b.top + (body?.clientTop ?? 0);
      const view = {
        left: Math.max(0, l) - p.left,
        top: Math.max(0, t) - p.top,
        right: Math.min(window.innerWidth, body ? l + body.clientWidth : p.right) - p.left,
        bottom: Math.min(window.innerHeight, body ? t + body.clientHeight : p.bottom) - p.top,
      };
      // Its height at the width it will have, with nothing cut
      const was = { width: card.style.width, maxHeight: card.style.maxHeight };
      card.style.width = `${cardWidth(view)}px`;
      card.style.maxHeight = "none";
      const size = { w: CARD_W, h: card.offsetHeight };
      Object.assign(card.style, was);
      const outline = { left: x0 * scale, top: y0 * scale, right: x1 * scale, bottom: y1 * scale };
      const at = { ...placeCard(outline, size, view), scrolls: false };
      at.scrolls = size.h > at.maxHeight;
      setPlace((o) => (o && (Object.keys(at) as (keyof typeof at)[]).every((k) => o[k] === at[k]) ? o : at));
    };
    fit();
    body?.addEventListener("scroll", fit, { passive: true });
    window.addEventListener("resize", fit);
    return () => { body?.removeEventListener("scroll", fit); window.removeEventListener("resize", fit); };
  }, [item, scale, x0, y0, x1, y1]);
  // Hidden until placed (before it's painted), and while its tag is scrolled out of sight
  const style = { left: place?.left ?? 0, top: place?.top ?? 0, width: place?.width, maxHeight: place?.maxHeight,
    ...(!place || place.side === "off" ? { visibility: "hidden" as const } : {}) };
  return (
    <div id={id} ref={ref} className={`pv-pop${place?.scrolls ? " scrolls" : ""}`} style={style} role="tooltip"
      data-side={place?.side} tabIndex={place?.scrolls ? 0 : undefined}
      onClick={(e) => e.stopPropagation()} // the page would take a click on it for one on blank page, and hide it
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onLeave(); }}>
      <div className="pv-pop-head">
        <strong>{ROLE_NAMES[item.role] ?? item.role}</strong> <span className="mono muted">{item.role}</span>
        {item.decorative && <span className="pill muted">decorative (pending)</span>}
        {item.pending && <span className="pill muted">not saved</span>}
      </div>
      {item.el.actual_text && !item.el.alt ? (
        <>
          <p className="small"><span className="muted">Read aloud as:</span> “{item.el.actual_text}”</p>
          {words && <p className="small muted">Drawn text: “{words}”</p>}
        </>
      ) : item.text !== undefined ? (item.text && <p className="small">“{item.text}”</p>)
        : words && <p className="small">“{words}”</p>}
      {item.el.name && !item.el.text && (
        <p className="small"><span className="muted">Announced as:</span> “{item.el.name}”</p>
      )}
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
