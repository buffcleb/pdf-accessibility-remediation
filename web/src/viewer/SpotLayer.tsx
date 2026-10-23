// The area a review item is about, highlighted on its page (#426), and the page's own list of items no tag holds
import { useEffect, useRef, useState } from "react";
import type { Finding } from "../api";
import type { Spot } from "./spot";

const LABEL_W = 300;

/** The item's area outlined (solid and dashed, so not by color alone), the rest of the page dimmed; a label naming
 * the item, which takes focus and closes with its button or Esc. A whole-page item outlines the page. */
export function SpotLayer({ spot, index, scale, w, h, onClose }: {
  spot: Spot; index: number; scale: number; w: number; h: number; onClose: () => void;
}) {
  const labelRef = useRef<HTMLDivElement>(null);
  useEffect(() => { labelRef.current?.focus({ preventScroll: true }); }, [spot]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      // Not while a dialog is open: its own Esc closes it
      if (e.key === "Escape" && !document.querySelector("dialog[open], [role=dialog]")) onClose();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  const first = spot.boxes[0];
  // Above the area when there's room, below it otherwise; inside the page across
  const top = !first ? 8 : first[1] * scale - 40 >= 0 ? first[1] * scale - 40 : Math.min(h - 36, first[3] * scale + 8);
  const left = Math.min(Math.max(4, (first?.[0] ?? 0) * scale), Math.max(4, w - LABEL_W - 4));
  return (
    <>
      {spot.boxes.length > 0 ? (
        <svg className="pv-veil pv-spot-veil" aria-hidden="true" width={w} height={h}>
          <defs>
            <mask id={`pv-spot-${index}`}>
              <rect width={w} height={h} fill="white" />
              {spot.boxes.map((b, n) => <rect key={n} x={b[0] * scale - 4} y={b[1] * scale - 4}
                width={(b[2] - b[0]) * scale + 8} height={(b[3] - b[1]) * scale + 8} fill="black" />)}
            </mask>
          </defs>
          <rect className="pv-veil-fill" width={w} height={h} mask={`url(#pv-spot-${index})`} />
        </svg>
      ) : null}
      {(spot.boxes.length ? spot.boxes : [null]).map((b, n) => (
        <div key={n} className={`pv-spot-box${b ? "" : " whole"}`} aria-hidden="true"
          style={b ? { left: b[0] * scale - 4, top: b[1] * scale - 4, width: (b[2] - b[0]) * scale + 8,
            height: (b[3] - b[1]) * scale + 8 } : undefined} />
      ))}
      <div className="pv-spot-label" ref={labelRef} tabIndex={-1} role="note" style={{ top, left, maxWidth: LABEL_W }}
        aria-label={`${spot.issue}, page ${spot.page}${spot.boxes.length ? "" : ", the whole page"}`}>
        <span>{spot.issue}{spot.boxes.length ? "" : " (the whole page)"}</span>
        <button type="button" className="pv-spot-close" onClick={onClose} aria-label="Close the highlight"
          title="Close the highlight (Esc)">×</button>
      </div>
    </>
  );
}

/** A page's items that no tag holds: a button that lists them, each one shown on the page when chosen. Without
 * onShow (the read-only pane) it only counts them. */
export function PageNotes({ notes, onShow }: { notes: Finding[]; onShow?: (f: Finding) => void }) {
  const [open, setOpen] = useState(false);
  const words = `${notes.length} page-level note${notes.length > 1 ? "s" : ""}`;
  if (!onShow) return <div className="pv-pagenote" title={notes.map((f) => f.issue).join("; ")}>{words}</div>;
  return (
    <div className="pv-pagenote-wrap" onClick={(e) => e.stopPropagation()}>
      <button type="button" className="pv-pagenote" aria-expanded={open} onClick={() => setOpen(!open)}>{words}</button>
      {open && (
        <ul className="pv-pagenotes">
          {notes.map((f, n) => (
            <li key={n}>
              <button type="button" onClick={() => { setOpen(false); onShow(f); }}>
                <span className="visually-hidden">Show </span>{f.issue}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
