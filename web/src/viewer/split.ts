/** The divider between the viewer's two panes: where it stands, as the left pane's share of the two (percent). */
export const SPLIT_MIN = 20;
export const SPLIT_MAX = 80;
export const SPLIT_DEFAULT = 50;
const KEY = "pv-split";

export function clampSplit(v: number): number {
  return Number.isFinite(v) ? Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, Math.round(v))) : SPLIT_DEFAULT;
}

/** The share for a pointer at `x`, with the left pane starting at `start` and the right one ending at `end`. */
export function splitAt(x: number, start: number, end: number): number {
  return end > start ? clampSplit(((x - start) / (end - start)) * 100) : SPLIT_DEFAULT;
}

/** What a key does to the divider, or null when the key isn't one of its keys. */
export function splitKey(key: string, now: number): number | null {
  if (key === "ArrowLeft") return clampSplit(now - 2);
  if (key === "ArrowRight") return clampSplit(now + 2);
  if (key === "Home") return SPLIT_MIN;
  if (key === "End") return SPLIT_MAX;
  if (key === "Enter") return SPLIT_DEFAULT;
  return null;
}

/** Kept in this browser, so the panes are as the person left them. */
export function loadSplit(storage: Pick<Storage, "getItem"> | null): number {
  try {
    const v = storage?.getItem(KEY);
    return v === null || v === undefined ? SPLIT_DEFAULT : clampSplit(Number(v));
  } catch {
    return SPLIT_DEFAULT;
  }
}

export function saveSplit(storage: Pick<Storage, "setItem"> | null, v: number): void {
  try { storage?.setItem(KEY, String(clampSplit(v))); } catch { /* private mode: the divider still works */ }
}

/** The editing panel beside the panes: its width in pixels, set with the divider at its left edge. */
export const SIDE_MIN = 260;
export const SIDE_MAX = 720;
export const SIDE_DEFAULT = 340;
const SIDE_KEY = "pv-side";

export function clampSide(v: number): number {
  return Number.isFinite(v) ? Math.min(SIDE_MAX, Math.max(SIDE_MIN, Math.round(v))) : SIDE_DEFAULT;
}

/** The width for a pointer at `x`, with the panel ending at `end` (the divider is `gap` wide, held at its middle). */
export function sideAt(x: number, end: number, gap = 10): number {
  return clampSide(end - x - gap / 2);
}

/** The panel is to the right of its divider, so the left arrow widens it. */
export function sideKey(key: string, now: number): number | null {
  if (key === "ArrowLeft") return clampSide(now + 16);
  if (key === "ArrowRight") return clampSide(now - 16);
  if (key === "Home") return SIDE_MAX;
  if (key === "End") return SIDE_MIN;
  if (key === "Enter") return SIDE_DEFAULT;
  return null;
}

export function loadSide(storage: Pick<Storage, "getItem"> | null): number {
  try {
    const v = storage?.getItem(SIDE_KEY);
    return v === null || v === undefined ? SIDE_DEFAULT : clampSide(Number(v));
  } catch {
    return SIDE_DEFAULT;
  }
}

export function saveSide(storage: Pick<Storage, "setItem"> | null, v: number): void {
  try { storage?.setItem(SIDE_KEY, String(clampSide(v))); } catch { /* private mode: the divider still works */ }
}
