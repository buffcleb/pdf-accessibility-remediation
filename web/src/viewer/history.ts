// The unsaved changes' history (#365): every change to the list of pending edits can be undone and redone in order,
// with Ctrl+Z and Ctrl+Shift+Z (Cmd on a Mac) or the heading's buttons. Loading a draft or saving starts afresh.

export type History<T> = { past: T[]; present: T; future: T[] };

/** How many steps back can be undone; older ones drop off. */
export const MAX_STEPS = 200;

export function fresh<T>(present: T): History<T> {
  return { past: [], present, future: [] };
}

/** A new state: the one before can be undone, and what was undone can no longer be redone. */
export function push<T>(h: History<T>, next: T): History<T> {
  if (next === h.present) return h;
  return { past: [...h.past, h.present].slice(-MAX_STEPS), present: next, future: [] };
}

export function undo<T>(h: History<T>): History<T> {
  if (!h.past.length) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

export function redo<T>(h: History<T>): History<T> {
  if (!h.future.length) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

/** The keyboard's undo and redo, outside text fields (where they undo typing): "undo", "redo" or null. */
export function historyKey(e: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean },
  target: EventTarget | null): "undo" | "redo" | null {
  if (e.altKey || !(e.ctrlKey || e.metaKey)) return null;
  const t = target as HTMLElement | null;
  if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName ?? ""))) return null;
  const k = e.key.toLowerCase();
  if (k === "z") return e.shiftKey ? "redo" : "undo";
  if (k === "y" && !e.shiftKey) return "redo";
  return null;
}
