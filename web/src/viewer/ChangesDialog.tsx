import { useEffect, useRef, type ReactNode } from "react";

/** A dialog opened from one of the viewer heading's buttons (the unsaved changes and saving them, or the AI review).
 * Escape or Close closes it. */
export function HeadingDialog({ title, className, children, onClose }: { title: string; className: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  // Where the focus was before the dialog: noted once, since an effect that runs twice would find it inside
  const opener = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const d = ref.current;
    const back = opener.current;
    if (d && !d.open) d.showModal();
    // The note to write, or Close: never a button that acts at once (Undo, Run the AI review) under a stray Enter
    (d?.querySelector<HTMLElement>("textarea") ?? d?.querySelector<HTMLElement>(":scope > .actions button"))?.focus();
    return () => { window.setTimeout(() => { if (!document.querySelector(`dialog.${className}[open]`)) back?.focus?.(); }, 0); };
  }, [className]);
  return (
    <dialog ref={ref} className={`confirm-dialog ${className}`} aria-label={title} onCancel={(e) => { e.preventDefault(); onClose(); }}>
      {children}
      <div className="actions">
        <button className="btn secondary" type="button" onClick={onClose}>Close</button>
      </div>
    </dialog>
  );
}
