import { useEffect, useRef, useState } from "react";
import type { TagEdit, TagElement } from "../api";
import { ROLE_NAMES } from "./draft";
import { ROW_KINDS, canIndent, canOutdent, changeLine, outlineEdit, outlineLines, outlineRows, outlineSummary, type RowKind } from "./outline";

const KIND_NAMES: Record<RowKind, string> = { item: "List item", P: "Paragraph", H1: "Heading 1", H2: "Heading 2", H3: "Heading 3",
  H4: "Heading 4", H5: "Heading 5", H6: "Heading 6" };
const short = (t: string) => (t.length > 40 ? `${t.slice(0, 40)}…` : t) || "empty line";

/** Build a list from the lines of the selected tag or tags. Each line is a row, placed by a first guess from
 * where it sits on the page; a row can be moved a level in or out, made a heading or paragraph that stays outside
 * the list, or joined to the row above when it is the rest of a wrapped line. Apply makes it one unsaved change. */
export function ListBuilder({ els, onApply, onClose }: { els: TagElement[]; onApply: (op: TagEdit) => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  const [lines, setLines] = useState(() => outlineLines(els));
  const [said, setSaid] = useState("");
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>(".pv-row button:not(:disabled), .pv-row select")?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-builder[open]")) back?.focus?.(); }, 0); };
  }, []);

  const rows = outlineRows(lines);
  const change = (i: number, c: Parameters<typeof changeLine>[2], what: string) => {
    const next = changeLine(lines, i, c);
    setLines(next);
    const l = next[i];
    setSaid(`${what}: ${!l.start ? "joined to the row above" : l.kind === "item" ? `list item, level ${l.indent + 1}` : KIND_NAMES[l.kind]}.`);
  };
  const what = els.length > 1 ? `the ${els.length} selected tags` : `this ${(ROLE_NAMES[els[0].role] ?? els[0].role).toLowerCase()}`;

  return (
    <dialog ref={ref} className="confirm-dialog pv-builder" aria-labelledby="pv-builder-h" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <h2 id="pv-builder-h">Build a list</h2>
      <p className="small muted">The lines of {what}, placed by where they sit on the page. Move a row in or out a level with its
        arrows (or Alt+Right and Alt+Left), or choose Heading or Paragraph for a row that isn't part of the list.
        A wrapped line belongs with the row above it.</p>
      <ol className="pv-rows">
        {rows.map(({ at, line, more }) => (
          <li key={at} className={`pv-row${line.kind === "item" ? "" : " plain"}`}
            onKeyDown={(e) => {
              if (!e.altKey || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
              e.preventDefault();
              change(at, { indent: e.key === "ArrowRight" ? 1 : -1 }, short(line.text));
            }}>
            <span className="pv-row-tools">
              <button type="button" className="btn secondary small" disabled={!canOutdent(lines, at)} title="Out a level"
                aria-label={`Move out a level: ${short(line.text)}`} onClick={() => change(at, { indent: -1 }, short(line.text))}>←</button>
              <button type="button" className="btn secondary small" disabled={!canIndent(lines, at)} title="In a level"
                aria-label={`Move in a level: ${short(line.text)}`} onClick={() => change(at, { indent: 1 }, short(line.text))}>→</button>
              <select value={line.kind} aria-label={`What this row is: ${short(line.text)}`}
                onChange={(e) => change(at, { kind: e.target.value as RowKind }, short(line.text))}>
                {ROW_KINDS.map((k) => <option key={k} value={k}>{KIND_NAMES[k]}</option>)}
              </select>
            </span>
            <span className="pv-row-body" style={{ paddingLeft: line.kind === "item" ? `${line.indent * 26}px` : 0 }}>
              <span className="pv-row-mark" aria-hidden="true">{line.kind === "item" ? "•" : line.kind}</span>
              <span className="pv-row-text">
                <span className="visually-hidden">{line.kind === "item" ? `Level ${line.indent + 1}: ` : `${KIND_NAMES[line.kind]}: `}</span>
                {line.text || <em>empty line</em>}
                {more.map((m) => (
                  <span key={m} className="pv-row-more">{lines[m].text}{" "}
                    <button type="button" className="linkbtn small" onClick={() => change(m, { start: true }, short(lines[m].text))}>Make this its own row</button>
                  </span>
                ))}
                {at > 0 && <>{" "}<button type="button" className="linkbtn small" onClick={() => change(at, { start: false }, short(line.text))}>Join with the row above</button></>}
              </span>
            </span>
          </li>
        ))}
      </ol>
      <p className="visually-hidden" role="status">{said}</p>
      <p className="small" role="status">This makes {outlineSummary(lines)}. Nothing is in the file until you save.</p>
      <div className="actions">
        <button className="btn" type="button" onClick={() => { onApply(outlineEdit(els, lines)); onClose(); }}>Apply</button>
        <button className="btn secondary" type="button" onClick={onClose}>Cancel</button>
      </div>
    </dialog>
  );
}
