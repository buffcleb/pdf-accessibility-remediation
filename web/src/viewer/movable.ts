import { useCallback, useEffect, useState, type RefObject } from "react";

/** Where a moved dialog is: dragged to a place, put against a side of the window, or where it opened (the middle). */
export type DialogPlace = "middle" | "right" | "left" | "dragged";

const MARGIN = 16;  // kept between a dialog put to a side and the window's edge
const SHOWN = 80;   // of a dragged dialog, at least this much stays in the window, so it can be dragged back

/** A modal dialog that can be moved out of the way of the page under it: by dragging its title (the `handle`), or with
 * a button that puts it against the right side of the window, then the left, then back in the middle (`next`): the
 * button is the way for the keyboard, and for anyone who can't drag. */
export function useMovable(ref: RefObject<HTMLDialogElement | null>, handle: string) {
  const [place, setPlace] = useState<DialogPlace>("middle");

  const put = useCallback((left: number | null, top: number | null) => {
    const d = ref.current;
    if (!d) return;
    if (left === null || top === null) {
      d.style.margin = d.style.left = d.style.top = "";
      return;
    }
    d.style.margin = "0";
    d.style.left = `${left}px`;
    d.style.top = `${top}px`;
  }, [ref]);

  const next = useCallback(() => {
    const d = ref.current;
    if (!d) return;
    const r = d.getBoundingClientRect();
    const top = Math.max(MARGIN, (window.innerHeight - r.height) / 2);
    const to: DialogPlace = place === "right" ? "left" : place === "left" ? "middle" : "right";
    if (to === "middle") put(null, null);
    else put(to === "right" ? Math.max(MARGIN, window.innerWidth - r.width - MARGIN) : MARGIN, top);
    setPlace(to);
  }, [place, put, ref]);

  useEffect(() => {
    const d = ref.current;
    const h = d?.querySelector<HTMLElement>(handle);
    if (!d || !h) return;
    h.classList.add("dlg-handle");
    let from: { x: number; y: number; left: number; top: number } | null = null;
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      const r = d.getBoundingClientRect();
      from = { x: e.clientX, y: e.clientY, left: r.left, top: r.top };
      h.setPointerCapture(e.pointerId);
      e.preventDefault();  // no text selected while dragging
    };
    const move = (e: PointerEvent) => {
      if (!from) return;
      const r = d.getBoundingClientRect();
      const left = Math.min(window.innerWidth - SHOWN, Math.max(SHOWN - r.width, from.left + e.clientX - from.x));
      const top = Math.min(window.innerHeight - SHOWN, Math.max(0, from.top + e.clientY - from.y));
      put(left, top);
      setPlace("dragged");
    };
    const up = () => { from = null; };
    h.addEventListener("pointerdown", down);
    h.addEventListener("pointermove", move);
    h.addEventListener("pointerup", up);
    h.addEventListener("pointercancel", up);
    return () => {
      h.removeEventListener("pointerdown", down);
      h.removeEventListener("pointermove", move);
      h.removeEventListener("pointerup", up);
      h.removeEventListener("pointercancel", up);
    };
  }, [handle, put, ref]);

  const label = place === "right" ? "Move to the left" : place === "left" ? "Back to the middle" : "Move to the side";
  return { place, next, label };
}
