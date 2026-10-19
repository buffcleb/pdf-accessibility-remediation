import { useEffect, useRef, useState } from "react";
import type { TagEdit, TagMap } from "../api";
import {
  addColumn, addRow, buildEdit, canMerge, fromLayout, fromOp, fromTable, headerCols, headerRows, merge, problems,
  rebuildEdit, removeColumn, removeRow, setHeaders, slots, split, toggleHeader, type Grid, type GridCell,
} from "./tablegrid";

const short = (t: string) => (t.length > 30 ? `${t.slice(0, 30)}…` : t);
const where = (x: GridCell) => `row ${x.r + 1}, column ${x.c + 1}`;
const CAPTION_FROM = ["P", "Caption", "H1", "H2", "H3", "H4", "H5", "H6"];

/** The table editor (#370): a table's cells in a grid, to choose its header rows and columns and caption, merge and
 * split cells, and add and remove rows and columns; or text tags laid out as a table, from where their pieces sit
 * on the page, to correct before making it one. Apply makes it one unsaved change. A removed row's or column's
 * content joins its neighbour. */
export function TableDialog({ map, ops, table, tags, onApply, onClose }: {
  map: TagMap; ops: TagEdit[]; table?: number; tags?: number[]; onApply: (op: TagEdit) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  const building = table === undefined;
  const [grid, setGrid] = useState<Grid>(() => {
    if (building) return fromLayout(map, tags ?? []);
    const pending = ops.find((o): o is Extract<TagEdit, { op: "rebuild_table" }> => o.op === "rebuild_table" && o.element === table);
    return pending ? fromOp(map, pending) : fromTable(map, table!);
  });
  const [chosen, setChosen] = useState<number | null>(grid.cells[0]?.key ?? null);
  const [said, setSaid] = useState("");
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>(".pv-tgrid button")?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-tabledlg[open]")) back?.focus?.(); }, 0); };
  }, []);

  const cell = grid.cells.find((x) => x.key === chosen) ?? null;
  const change = (next: Grid, what: string, keep?: number | null) => {
    setGrid(next);
    if (keep !== undefined) setChosen(keep);
    else if (!next.cells.some((x) => x.key === chosen)) setChosen(next.cells[0]?.key ?? null);
    setSaid(what);
  };
  const s = slots(grid);
  const wrong = problems(grid);
  const hr = headerRows(grid), hc = headerCols(grid);
  const nearby = (() => {
    if (building || table === undefined) return [];
    const t = map.elements[table];
    const parent = t.parent !== null ? map.elements[t.parent] : null;
    const sib = parent?.kids ?? [];
    const at = sib.indexOf(table);
    const around = [sib[at - 1], sib[at + 1]].filter((k) => k !== undefined).map((k) => map.elements[k])
      .filter((e) => e && CAPTION_FROM.includes(e.role));
    const own = t.kids.map((k) => map.elements[k]).filter((e) => e?.role === "Caption");
    return [...own, ...around];
  })();

  return (
    <dialog ref={ref} className="confirm-dialog pv-tabledlg" aria-labelledby="pv-table-h" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <h2 id="pv-table-h">{building ? "Make a table" : "Edit table"}</h2>
      <p className="small muted">{building
        ? "The selected tags' pieces of text, placed in rows and columns by where they sit on the page. Correct it here: merge, split, add or remove rows and columns. A line drawn as one piece stays in one cell; split the tag first to spread it over several."
        : "Choose a cell to merge or split it, or to add or remove its row or column. A removed row's content joins the row above (a removed column's, the column to its left), so nothing on the page goes untagged."}</p>

      <div className="pv-tgrid-tools">
        <label>Header rows <input type="number" min={0} max={grid.height} value={hr}
          onChange={(e) => change(setHeaders(grid, Math.max(0, Math.min(grid.height, Number(e.target.value) || 0)), hc), "Header rows changed.")} /></label>
        <label>Header columns <input type="number" min={0} max={grid.width} value={hc}
          onChange={(e) => change(setHeaders(grid, hr, Math.max(0, Math.min(grid.width, Number(e.target.value) || 0))), "Header columns changed.")} /></label>
        {!building && (
          <label>Caption <select value={grid.caption ?? ""} onChange={(e) => change({ ...grid, caption: e.target.value === "" ? null : Number(e.target.value) }, "Caption changed.")}>
            <option value="">None</option>
            {nearby.map((e) => <option key={e.i} value={e.i}>{short(e.text ?? `${e.role} tag`)}</option>)}
          </select></label>
        )}
      </div>

      <div className="pv-tgrid-wrap" tabIndex={0} role="region" aria-label="The table's cells">
        <table className="pv-tgrid">
          <tbody>
            {s.map((row, r) => (
              <tr key={r}>
                {row.map((k, c) => {
                  const x = grid.cells.find((y) => y.key === k);
                  if (!x || x.r !== r || x.c !== c) return null;  // covered by a cell that spans here
                  return (
                    <td key={c} rowSpan={x.rows} colSpan={x.cols} className={`${x.role === "TH" ? "th" : ""}${x.key === chosen ? " on" : ""}`}>
                      <button type="button" aria-pressed={x.key === chosen} onClick={() => setChosen(x.key)}
                        aria-label={`${x.role === "TH" ? "Header" : "Cell"}, ${where(x)}${x.rows > 1 || x.cols > 1 ? `, spanning ${x.rows} by ${x.cols}` : ""}: ${x.text || "empty"}`}>
                        {x.text ? short(x.text) : <em className="muted">empty</em>}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {cell && (
        <div className="pv-tgrid-tools" role="group" aria-label={`The chosen cell: ${where(cell)}`}>
          <span className="small">{where(cell)}:</span>
          <button type="button" className="btn secondary small" disabled={!canMerge(grid, cell.key, "right")}
            onClick={() => change(merge(grid, cell.key, "right"), "Merged with the cell to its right.", cell.key)}>Merge right</button>
          <button type="button" className="btn secondary small" disabled={!canMerge(grid, cell.key, "down")}
            onClick={() => change(merge(grid, cell.key, "down"), "Merged with the cell below.", cell.key)}>Merge down</button>
          <button type="button" className="btn secondary small" disabled={cell.rows === 1 && cell.cols === 1}
            onClick={() => change(split(grid, cell.key), "Split: the places it covered are empty cells.", cell.key)}>Split</button>
          <button type="button" className="btn secondary small" onClick={() => change(toggleHeader(grid, cell.key), cell.role === "TH" ? "Now a data cell." : "Now a header.", cell.key)}>
            {cell.role === "TH" ? "Make it a data cell" : "Make it a header"}</button>
          <button type="button" className="btn secondary small" onClick={() => change(addRow(grid, cell.r + cell.rows - 1), "Row added below.", cell.key)}>Add row below</button>
          <button type="button" className="btn secondary small" onClick={() => change(addColumn(grid, cell.c + cell.cols - 1), "Column added to the right.", cell.key)}>Add column right</button>
          <button type="button" className="btn secondary small" disabled={grid.height < 2}
            onClick={() => change(removeRow(grid, cell.r), `Row ${cell.r + 1} removed; its content joined the row ${cell.r === 0 ? "below" : "above"}.`)}>Remove row</button>
          <button type="button" className="btn secondary small" disabled={grid.width < 2}
            onClick={() => change(removeColumn(grid, cell.c), `Column ${cell.c + 1} removed; its content joined the column ${cell.c === 0 ? "to its right" : "to its left"}.`)}>Remove column</button>
        </div>
      )}
      <p className="small muted">{grid.height} row{grid.height === 1 ? "" : "s"} by {grid.width} column{grid.width === 1 ? "" : "s"}
        {hr || hc ? `; ${[hr ? `${hr} header row${hr === 1 ? "" : "s"}` : "", hc ? `${hc} header column${hc === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ")}` : "; no headers"}.</p>
      {wrong && <p className="small bad-text" role="alert">{wrong}</p>}
      <span className="visually-hidden" aria-live="polite">{said}</span>
      <div className="actions">
        <button type="button" className="btn" disabled={!!wrong}
          onClick={() => onApply(building ? buildEdit(map, tags ?? [], grid) : rebuildEdit(map, table!, grid))}>Apply</button>
        <button type="button" className="btn secondary" onClick={onClose}>Cancel</button>
      </div>
    </dialog>
  );
}
