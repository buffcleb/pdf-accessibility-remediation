import { useCallback, useEffect, useId, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { DialogEdits } from "./unsaved";

/** The admin area's edit dialog (#511): a form over the page instead of below it. Escape, the close button or a click
 * outside close it straight away when nothing has changed; with changes (any form inside calling useUnsavedChanges),
 * it asks first: save, discard, or keep editing. `formId`: the form to submit from that question (its Save). Focus
 * goes into the dialog and back to what opened it. */
export function AdminDialog({ title, onClose, formId, wide = false, children }: {
  title: string; onClose: () => void; formId?: string; wide?: boolean; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const edits = useRef(new Set<string>());
  const [asking, setAsking] = useState(false);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    opener.current = document.activeElement;
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => { (opener.current as HTMLElement | null)?.focus?.(); };
  }, []);

  const context = useMemo(() => ({ add: (id: string) => edits.current.add(id), remove: (id: string) => edits.current.delete(id) }), []);
  const requestClose = useCallback(() => {
    if (edits.current.size > 0) setAsking(true); else onClose();
  }, [onClose]);

  // A click on the backdrop lands on the dialog element itself, outside its box
  function outside(e: MouseEvent<HTMLDialogElement>) {
    if (e.target !== ref.current) return;
    const r = ref.current.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) requestClose();
  }

  return (
    <dialog ref={ref} className={`confirm-dialog admin-dialog${wide ? " wide" : ""}`} aria-labelledby={headingId}
      onCancel={(e) => { e.preventDefault(); requestClose(); }} onClick={outside}>
      <div className="admin-dialog-head">
        <h2 id={headingId}>{title}</h2>
        <button type="button" className="admin-dialog-close" aria-label="Close" onClick={requestClose}>×</button>
      </div>
      <DialogEdits.Provider value={context}>{children}</DialogEdits.Provider>
      {asking && <DiscardQuestion canSave={!!formId} onSave={() => {
        setAsking(false);
        // The form's own submit: its checks run, and a successful save closes the dialog as usual
        (document.getElementById(formId!) as HTMLFormElement | null)?.requestSubmit();
      }}
        onDiscard={() => { setAsking(false); edits.current.clear(); onClose(); }} onKeep={() => setAsking(false)} />}
    </dialog>
  );
}

function DiscardQuestion({ canSave, onSave, onDiscard, onKeep }: {
  canSave: boolean; onSave: () => void; onDiscard: () => void; onKeep: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="discard-h" aria-describedby="discard-d"
      onCancel={(e) => { e.preventDefault(); onKeep(); }}>
      <h2 id="discard-h">Save your changes?</h2>
      <p id="discard-d">You've changed this form. If you close it without saving, the changes are lost.</p>
      <div className="actions">
        {canSave && <button className="btn" type="button" autoFocus onClick={onSave}>Save</button>}
        <button className={`btn${canSave ? " secondary" : ""}`} type="button" autoFocus={!canSave} onClick={onKeep}>Keep editing</button>
        <button className="btn secondary danger-text" type="button" onClick={onDiscard}>Discard changes</button>
      </div>
    </dialog>
  );
}

/** A button that opens an admin edit dialog (#511), for adding something: `children` gets `close`, for after a save. */
export function DialogButton({ label, title, formId, wide, className = "btn secondary", children }: {
  label: ReactNode; title: string; formId?: string; wide?: boolean; className?: string;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  return (
    <>
      <button type="button" className={className} aria-haspopup="dialog" onClick={() => setOpen(true)}>{label}</button>
      {open && <AdminDialog title={title} formId={formId} wide={wide} onClose={close}>{children(close)}</AdminDialog>}
    </>
  );
}
