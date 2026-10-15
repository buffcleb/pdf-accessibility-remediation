import { useEffect, useRef } from "react";
import type { MenuItem } from "./menu";

/** The types the selected tag or tags can be changed to, opened from the tag menu's "Change type…". Each says
 * what else choosing it does, or why it can't be chosen. Escape or Cancel closes it without a change. */
export function TypeDialog({ title, types, onChoose, onClose }: {
  title: string; types: MenuItem[]; onChoose: (item: MenuItem) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLButtonElement>(".pv-type:not([aria-disabled])")?.focus();
    // The dialog is taken away rather than closed, so the focus is put back by hand, once the page behind it
    // can take the focus again
    return () => { window.setTimeout(() => { if (!document.querySelector("dialog.pv-types[open]")) back?.focus?.(); }, 0); };
  }, []);
  return (
    <dialog ref={ref} className="confirm-dialog pv-types" aria-labelledby="pv-types-h"
      onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <h2 id="pv-types-h">Change type</h2>
      <p className="small muted">{title}. Nothing is in the file until you save.</p>
      <div className="pv-type-list">
        {types.map((t) => (
          <button key={t.id} type="button" className="pv-type" aria-disabled={t.disabled ? "true" : undefined}
            onClick={() => { if (!t.disabled) { onChoose(t); onClose(); } }}>
            <span className="pv-type-name">{t.label}</span>
            {(t.disabled ?? t.note) && <span className="pv-menu-note">{t.disabled ?? t.note}</span>}
          </button>
        ))}
      </div>
      <div className="actions">
        <button className="btn secondary" type="button" onClick={onClose}>Cancel</button>
      </div>
    </dialog>
  );
}
