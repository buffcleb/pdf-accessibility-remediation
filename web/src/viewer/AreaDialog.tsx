import { useEffect, useRef, type ComponentProps } from "react";
import { AreaEdit } from "./EditPanel";

/** What's in the chosen area of a page, in a dialog that opens when the area is drawn or a whole page is chosen: move
 * tagged content into the selected tag (a tag named in the list can be selected from it), or tag content that isn't
 * read. "Draw an area" closes it to draw again. Done, or Escape, closes it and clears the area. */
export function AreaDialog({ onClose, ...props }: ComponentProps<typeof AreaEdit> & { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>("input:not(:disabled), button")?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-areadlg[open]")) back?.focus?.(); }, 0); };
  }, []);
  return (
    <dialog ref={ref} className="confirm-dialog pv-areadlg" aria-labelledby="areadlg-h" onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <AreaEdit {...props} prefix="areadlg" />
      <div className="actions">
        <button className="btn secondary" type="button" onClick={onClose}>Done</button>
      </div>
    </dialog>
  );
}
