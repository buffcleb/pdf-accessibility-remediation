import { useEffect, useRef, type ComponentProps } from "react";
import type { TagEdit } from "../api";
import { AreaEdit } from "./EditPanel";
import { useMovable } from "./movable";
import { justMade } from "./pending";

/** What's in the chosen area of a page, in a dialog that opens when the area is drawn or a whole page is chosen, in
 * tabs: move tagged content into the selected tag (a tag named in the list can be selected from it), make a new tag
 * from content that isn't read, or Suggest tags with AI. Making a tag is the end of it: the dialog closes, and the new
 * tag is the one chosen (TagViewer). "Draw an area" closes it to draw again. Its × or Escape closes it and clears the
 * area. Like Edit tag, it moves out of the way of the page: drag its title, or "Move to the side". */
export function AreaDialog({ onClose, ...props }: ComponentProps<typeof AreaEdit> & { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  const move = useMovable(ref, "#areadlg-h");
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>("input:not(:disabled), button")?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-areadlg[open]")) back?.focus?.(); }, 0); };
  }, []);
  const setOps = (next: TagEdit[]) => {
    props.setOps(next);
    if (justMade(props.ops, next) !== null) onClose();
  };
  return (
    <dialog ref={ref} className="confirm-dialog pv-areadlg" aria-labelledby="areadlg-h" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <div className="dlg-tools">
        <button type="button" className="btn secondary small dlg-move" onClick={move.next}>{move.label}</button>
        <button type="button" className="dlg-close" aria-label="Close" title="Close (Esc)" onClick={onClose}>×</button>
      </div>
      <AreaEdit {...props} setOps={setOps} prefix="areadlg" />
    </dialog>
  );
}
