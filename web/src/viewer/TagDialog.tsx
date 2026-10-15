import { useEffect, useRef } from "react";
import type { Finding, TagEdit, TagElement, TagMap } from "../api";
import { TagForm } from "./EditPanel";

/** The selected tag in a dialog, opened from the tag menu's "Edit tag…": what it is and says, its findings, and its
 * type, alt text, read-aloud text and language to change, as in the panel, without the panel's room. Adding a change
 * closes it; Escape or Close closes it without one. */
export function TagDialog({ map, element, findings, ops, setOps, canEdit, onSelect, onClose }: {
  map: TagMap; element: TagElement; findings: (Finding & { extra?: { suggested_alt?: string } })[]; ops: TagEdit[];
  setOps: (o: TagEdit[]) => void; canEdit: boolean; onSelect: (i: number) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    // The words are what people come to change (alt text, read-aloud text); the type picker when there are none
    (d?.querySelector<HTMLElement>("textarea") ?? d?.querySelector<HTMLElement>("select, input"))?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-tagdlg[open]")) back?.focus?.(); }, 0); };
  }, []);
  return (
    <dialog ref={ref} className="confirm-dialog pv-tagdlg" aria-labelledby="dlg-h" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <TagForm map={map} element={element} findings={findings} ops={ops} setOps={setOps} canEdit={canEdit} prefix="dlg"
        onSelect={(i) => { onSelect(i); onClose(); }} onDone={onClose} />
      <div className="actions">
        <button className="btn secondary" type="button" onClick={onClose}>Close</button>
      </div>
    </dialog>
  );
}
