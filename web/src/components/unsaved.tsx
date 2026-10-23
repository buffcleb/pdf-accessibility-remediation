import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useBlocker } from "react-router-dom";

/* "You have unsaved changes": forms say when they hold edits with useUnsavedChanges(dirty). One provider owns the
 * router's blocker (a router supports only one) and the browser's reload/close warning, and asks before leaving. */

type Registry = { add: (id: string) => void; remove: (id: string) => void; confirmLeave: () => Promise<boolean> };

const Ctx = createContext<Registry | null>(null);

/** Navigate with this state to skip the question, e.g. straight after a successful save. */
export const SAVED = { skipUnsaved: true };

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const dirty = useRef(new Set<string>());
  const [asking, setAsking] = useState<{ leave: () => void; stay: () => void } | null>(null);

  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty.current.size > 0
    && !(nextLocation.state as { skipUnsaved?: boolean } | null)?.skipUnsaved
    && (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search));

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    setAsking({
      leave: () => { dirty.current.clear(); blocker.proceed(); },
      stay: () => blocker.reset(),
    });
  }, [blocker]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty.current.size === 0) return;
      e.preventDefault();
      e.returnValue = ""; // older browsers
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  const confirmLeave = useCallback(() => {
    if (dirty.current.size === 0) return Promise.resolve(true);
    return new Promise<boolean>((resolve) => setAsking({
      leave: () => { dirty.current.clear(); resolve(true); },
      stay: () => resolve(false),
    }));
  }, []);

  const registry = useMemo<Registry>(() => ({
    add: (id) => dirty.current.add(id),
    remove: (id) => dirty.current.delete(id),
    confirmLeave,
  }), [confirmLeave]);

  return (
    <Ctx.Provider value={registry}>
      {children}
      {asking && <LeaveDialog onLeave={() => { setAsking(null); asking.leave(); }} onStay={() => { setAsking(null); asking.stay(); }} />}
    </Ctx.Provider>
  );
}

/** The admin edit dialog a form is in (#511): it asks before closing while any form in it holds edits. */
export const DialogEdits = createContext<{ add: (id: string) => void; remove: (id: string) => void } | null>(null);

/** Mark this form as holding unsaved edits while `dirty` is true (for leaving the page, and for closing the dialog
 * it's in). */
export function useUnsavedChanges(dirty: boolean) {
  const registry = useContext(Ctx);
  const dialog = useContext(DialogEdits);
  const id = useId();
  useEffect(() => {
    if (!dirty) return;
    registry?.add(id);
    dialog?.add(id);
    return () => { registry?.remove(id); dialog?.remove(id); };
  }, [dirty, registry, dialog, id]);
}

/** For actions that leave the page without navigating first (signing out): resolves true to go ahead. */
export function useConfirmLeave() {
  const registry = useContext(Ctx);
  return registry?.confirmLeave ?? (() => Promise.resolve(true));
}

function LeaveDialog({ onLeave, onStay }: { onLeave: () => void; onStay: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="leave-h" aria-describedby="leave-d"
      onCancel={(e) => { e.preventDefault(); onStay(); }}>
      <h2 id="leave-h">Leave without saving?</h2>
      <p id="leave-d">You have changes on this page that aren't saved. If you leave now, they're lost.</p>
      <div className="actions">
        <button className="btn" type="button" autoFocus onClick={onStay}>Stay on this page</button>
        <button className="btn danger" type="button" onClick={onLeave}>Leave without saving</button>
      </div>
    </dialog>
  );
}
