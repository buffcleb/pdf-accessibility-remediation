import { useEffect, useMemo, useRef, useState } from "react";
import type { TagEdit, TagElement, TagMap } from "../api";
import { ROLE_NAMES } from "./draft";
import { NEEDS_ALT, bySize, figureEdits, figureFrame, figurePieces, groupProblem, pieceAt, pieceKey, piecesIn, type PieceGroup } from "./figure";
import type { PDFDoc } from "./pdf";

const ROLES = ["Figure", "Formula", "Caption", "P"];
const KIND = { text: "text", image: "picture", drawing: "drawing" } as Record<string, string>;

/** Split a figure that holds several drawings: the figure is shown with each of its pieces outlined. Click the pieces
 * that belong together (a drawing and its labels), make them a figure (or caption, formula, paragraph) of their own,
 * and repeat; what's left stays in the figure. Apply adds the changes; Cancel drops them. */
export function FigureSplit({ map, element, pdf, preselect, onApply, onClose, describe }: {
  map: TagMap; element: TagElement; pdf: PDFDoc; preselect?: string[]; onApply: (ops: TagEdit[]) => void; onClose: () => void;
  // Alt text for an area of the page from the AI (one call at the document's AI level), for people it's allowed for
  describe?: (page: number, bbox: [number, number, number, number]) => Promise<string>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  const pieces = useMemo(() => figurePieces(map, element), [map, element]);
  const page = pieces[0]?.page ?? 1;
  const size = map.pages[page - 1];
  const frame = useMemo(() => figureFrame(pieces, size.w, size.h), [pieces, size]);
  const scale = Math.min(3, 640 / Math.max(1, frame[2] - frame[0]), 420 / Math.max(1, frame[3] - frame[1]));
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(preselect ?? []));
  const [groups, setGroups] = useState<PieceGroup[]>([]);
  const [role, setRole] = useState("Figure");
  const [alt, setAlt] = useState("");
  const [keepAlt, setKeepAlt] = useState(element.alt ?? "");
  const [drawn, setDrawn] = useState<"yes" | "no" | "wait">("wait");
  const [asking, setAsking] = useState<"" | "busy" | string>(""); // the AI describing: busy, or what went wrong
  // A box being dragged over the picture, in the picture's pixels (x0, y0 where it started)
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number; add: boolean } | null>(null);
  // Pick whole pieces, or cut out exactly a box: for a scheme drawn as a few big pieces that run across its parts
  const [mode, setMode] = useState<"pieces" | "box">(() => (pieces.length < 2 ? "box" : "pieces"));
  const [cut, setCut] = useState<[number, number, number, number] | null>(null); // the box to cut, page units
  const taken = new Map(groups.flatMap((g, n) => g.keys.map((k) => [k, n] as const)));
  const problem = mode === "box" && !cut ? "Drag a box over the part to cut out first."
    : groupProblem(pieces, new Set(taken.keys()), chosen, role, alt, mode === "box");

  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>(".pv-fig-piece")?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-figdlg[open]")) back?.focus?.(); }, 0); };
  }, []);

  // The part of the page the figure covers, drawn at the dialog's size
  useEffect(() => {
    let task: { cancel: () => void; promise: Promise<void> } | null = null;
    let live = true;
    (async () => {
      try {
        const p = await pdf.getPage(page);
        const c = canvas.current;
        if (!live || !c) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const base = p.getViewport({ scale: 1 });
        const s = (size.w / base.width) * scale * dpr;
        const viewport = p.getViewport({ scale: s, offsetX: -frame[0] * scale * dpr, offsetY: -frame[1] * scale * dpr });
        c.width = Math.ceil((frame[2] - frame[0]) * scale * dpr);
        c.height = Math.ceil((frame[3] - frame[1]) * scale * dpr);
        task = p.render({ canvas: c, viewport });
        await task.promise;
        if (live) setDrawn("yes");
      } catch { if (live) setDrawn("no"); }
    })();
    return () => { live = false; task?.cancel(); };
  }, [pdf, page, frame, scale, size]);

  const toggle = (k: string) => setChosen((c) => { const n = new Set(c); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  // The picture takes the pointer: a click picks the smallest piece under it (so pieces inside bigger ones can be
  // picked), a drag draws a box and picks every piece inside it (with Shift, Ctrl or Cmd, as well as those picked)
  const at = (e: React.PointerEvent) => { const r = e.currentTarget.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const toPage = (x: number, y: number) => [x / scale + frame[0], y / scale + frame[1]] as const;
  // The area the AI is shown: the box to cut, or round the pieces picked
  const askArea = (): [number, number, number, number] | null => {
    if (mode === "box") return cut;
    const picked = pieces.filter((p) => chosen.has(pieceKey(p)));
    if (!picked.length) return null;
    return [Math.min(...picked.map((p) => p.bbox[0])), Math.min(...picked.map((p) => p.bbox[1])),
      Math.max(...picked.map((p) => p.bbox[2])), Math.max(...picked.map((p) => p.bbox[3]))];
  };
  const ask = async () => {
    const area = askArea();
    if (!describe || !area) return;
    setAsking("busy");
    try {
      setAlt(await describe(page, area));
      setAsking("");
    } catch (e) {
      setAsking(e instanceof Error ? e.message : "The AI couldn't describe it just now.");
    }
  };
  const make = () => {
    if (problem) return;
    setGroups([...groups, mode === "box" && cut ? { keys: [], role, alt, box: cut } : { keys: [...chosen], role, alt }]);
    setChosen(new Set());
    setCut(null);
    setAlt("");
  };
  const edits = figureEdits(element, pieces, groups, keepAlt);
  const w = (frame[2] - frame[0]) * scale, h = (frame[3] - frame[1]) * scale;

  return (
    <dialog ref={ref} className="confirm-dialog pv-figdlg" aria-labelledby="figdlg-h" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <h2 id="figdlg-h">Split the {(ROLE_NAMES[element.role] ?? element.role).toLowerCase()}</h2>
      <p className="small muted">Click the pieces that belong together (a drawing and its labels), or drag a box round them, then make
        them a tag of their own. A click picks the smallest piece under the pointer; Shift-drag adds to what's picked.
        Do the same for each part. What you don't take stays in this {element.role === "Formula" ? "formula" : "figure"}. Page {page}.</p>
      <div className="pv-fig-mode" role="radiogroup" aria-label="How to choose">
        <label className="check small"><input type="radio" name="figdlg-mode" checked={mode === "pieces"} onChange={() => { setMode("pieces"); setCut(null); }} />
          <span>Pick pieces <span className="muted">(click them, or drag a box round whole pieces)</span></span></label>
        <label className="check small"><input type="radio" name="figdlg-mode" checked={mode === "box"} onChange={() => { setMode("box"); setChosen(new Set()); }} />
          <span>Cut a box <span className="muted">(exactly what's inside it, even through a piece that runs across the figure)</span></span></label>
      </div>
      <div className="pv-fig-frame" style={{ width: w, height: h }}>
        <canvas ref={canvas} style={{ width: w, height: h }} aria-hidden="true" />
        <div className="pv-fig-pick" aria-hidden="true"
          onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); const p = at(e); setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y, add: e.shiftKey || e.ctrlKey || e.metaKey }); }}
          onPointerMove={(e) => { if (drag) { const p = at(e); setDrag({ ...drag, x1: p.x, y1: p.y }); } }}
          onPointerUp={() => {
            if (!drag) return;
            if (mode === "box") {
              // The box itself is what gets cut out
              if (Math.abs(drag.x1 - drag.x0) >= 4 && Math.abs(drag.y1 - drag.y0) >= 4) {
                const [ax, ay] = toPage(Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1));
                const [bx, by] = toPage(Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1));
                setCut([ax, ay, bx, by]);
              }
              setDrag(null);
              return;
            }
            const skip = new Set(taken.keys());
            if (Math.abs(drag.x1 - drag.x0) < 4 && Math.abs(drag.y1 - drag.y0) < 4) {
              const k = pieceAt(pieces, ...toPage(drag.x0, drag.y0), skip);
              if (k) toggle(k);
            } else {
              const inside = piecesIn(pieces, [...toPage(drag.x0, drag.y0), ...toPage(drag.x1, drag.y1)], skip);
              setChosen((c) => new Set([...(drag.add ? c : []), ...inside]));
            }
            setDrag(null);
          }}
          onPointerCancel={() => setDrag(null)} />
        {[...groups.map((g, n) => ({ box: g.box, n })), ...(cut ? [{ box: cut, n: -1 }] : [])].filter((b) => b.box).map(({ box, n }) => (
          <div key={n} className={`pv-fig-cut${n >= 0 ? ` g${n % 6}` : ""}`} aria-hidden="true"
            style={{ left: (box![0] - frame[0]) * scale, top: (box![1] - frame[1]) * scale, width: (box![2] - box![0]) * scale, height: (box![3] - box![1]) * scale }}>
            {n >= 0 && <span className="pv-fig-no">{n + 1}</span>}
          </div>
        ))}
        {drag && (Math.abs(drag.x1 - drag.x0) >= 4 || Math.abs(drag.y1 - drag.y0) >= 4) && (
          <div className="pv-fig-box" aria-hidden="true" style={{ left: Math.min(drag.x0, drag.x1), top: Math.min(drag.y0, drag.y1),
            width: Math.abs(drag.x1 - drag.x0), height: Math.abs(drag.y1 - drag.y0) }} />
        )}
        {drawn === "no" && <p className="small muted pv-fig-nodraw">The page couldn't be drawn here; the pieces are still outlined.</p>}
        {bySize(pieces).map((p) => {
          const n = pieces.indexOf(p);
          const k = pieceKey(p);
          const g = taken.get(k);
          const label = `${KIND[p.kind] ?? p.kind} ${n + 1}${p.text ? `: ${p.text.slice(0, 40)}` : ""}${g !== undefined ? `, in new tag ${g + 1}` : ""}`;
          return (
            <button key={k} type="button" className={`pv-fig-piece${g !== undefined ? ` taken g${g % 6}` : ""}`} aria-pressed={chosen.has(k)}
              disabled={g !== undefined} aria-label={label} title={label} onClick={() => toggle(k)}
              style={{ left: (p.bbox[0] - frame[0]) * scale - 2, top: (p.bbox[1] - frame[1]) * scale - 2,
                width: Math.max(6, (p.bbox[2] - p.bbox[0]) * scale + 4), height: Math.max(6, (p.bbox[3] - p.bbox[1]) * scale + 4) }}>
              {g !== undefined && <span className="pv-fig-no" aria-hidden="true">{g + 1}</span>}
            </button>
          );
        })}
      </div>
      <div className="pv-fig-tools">
        <span className="small" role="status">{mode === "box" ? (cut ? "A box is drawn: each line goes with the side holding its middle." : "Drag a box over the part to cut out.")
          : `${chosen.size} of ${pieces.length - taken.size} pieces selected`}</span>
        <button type="button" className="linkbtn small" onClick={() => setChosen(new Set(pieces.filter((p) => !taken.has(pieceKey(p))).map(pieceKey)))}>All</button>
        <button type="button" className="linkbtn small" onClick={() => setChosen(new Set())}>None</button>
      </div>
      <div className="pv-fig-make">
        <div className="field">
          <label htmlFor="figdlg-role" className="small">Make them a</label>
          <select id="figdlg-role" value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => <option key={r} value={r}>{ROLE_NAMES[r] ?? r}</option>)}
          </select>
        </div>
        {NEEDS_ALT.includes(role) && (
          <div className="field">
            <label htmlFor="figdlg-alt" className="small">Alt text for the new {role === "Figure" ? "figure" : "formula"}</label>
            <textarea id="figdlg-alt" rows={2} value={alt} maxLength={4000} onChange={(e) => setAlt(e.target.value)} />
            {describe && (
              <span className="pv-fig-ask">
                <button type="button" className="btn secondary small" disabled={asking === "busy" || !askArea()} onClick={() => void ask()}>
                  {asking === "busy" ? "Describing…" : "Describe with AI"}
                </button>
                <span className="hint">{!askArea() ? (mode === "box" ? "Draw the box first." : "Pick the pieces first.")
                  : "One AI call at this document's AI level. Check what it writes."}</span>
              </span>
            )}
            {asking && asking !== "busy" && <span className="small" role="alert">{asking}</span>}
          </div>
        )}
        <button type="button" className="btn small" disabled={!!problem} onClick={make}>
          {mode === "box" ? `Cut the box into a new ${ROLE_NAMES[role] ?? role}`
            : `Make a new ${ROLE_NAMES[role] ?? role} from ${chosen.size || "the selected"} piece${chosen.size === 1 ? "" : "s"}`}
        </button>
        {problem && (chosen.size > 0 || (mode === "box" && cut)) && <p className="small muted">{problem}</p>}
      </div>
      {groups.length > 0 && (
        <ol className="pv-fig-groups small">
          {groups.map((g, n) => (
            <li key={n}>
              <span className={`pv-fig-swatch g${n % 6}`} aria-hidden="true" /> New {ROLE_NAMES[g.role] ?? g.role} {n + 1}: {g.box ? "the box drawn" : `${g.keys.length} piece${g.keys.length > 1 ? "s" : ""}`}
              {g.alt ? `, “${g.alt.slice(0, 50)}${g.alt.length > 50 ? "…" : ""}”` : ""}{" "}
              <button type="button" className="linkbtn small" onClick={() => setGroups(groups.filter((_, m) => m !== n))}>Undo</button>
            </li>
          ))}
        </ol>
      )}
      <div className="field">
        <label htmlFor="figdlg-keep" className="small">Alt text for what stays in this {element.role === "Formula" ? "formula" : "figure"}</label>
        <textarea id="figdlg-keep" rows={2} value={keepAlt} maxLength={4000} onChange={(e) => setKeepAlt(e.target.value)} />
        <span className="hint">It described the whole {element.role === "Formula" ? "formula" : "figure"}; describe only what's left once parts are taken out.</span>
      </div>
      <div className="actions">
        <button className="btn" type="button" disabled={!edits.length} onClick={() => { onApply(edits); onClose(); }}>Apply</button>
        <button className="btn secondary" type="button" onClick={onClose}>Cancel</button>
      </div>
    </dialog>
  );
}
