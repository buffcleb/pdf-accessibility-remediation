import { useEffect, useRef } from "react";
import type { TagElement } from "../api";
import { ROLE_NAMES } from "./draft";
import { SplitPicker } from "./EditPanel";

/** Split a tag inside its text, in a dialog opened from the tag menu's "Split…": click where the new tag should
 * start. Splitting closes it; Escape or Cancel closes it without a change. */
export function SplitDialog({ element, onSplit, onClose }: { element: TagElement; onSplit: (at: number, char: number) => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>("textarea")?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-splitdlg[open]")) back?.focus?.(); }, 0); };
  }, []);
  return (
    <dialog ref={ref} className="confirm-dialog pv-splitdlg" aria-labelledby="splitdlg-h" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <h2 id="splitdlg-h">Split this {(ROLE_NAMES[element.role] ?? element.role).toLowerCase()}</h2>
      <SplitPicker element={element} prefix="splitdlg" onSplit={(at, char) => { onSplit(at, char); onClose(); }} />
      <div className="actions">
        <button className="btn secondary" type="button" onClick={onClose}>Cancel</button>
      </div>
    </dialog>
  );
}
