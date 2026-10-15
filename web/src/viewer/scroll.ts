/** Where to scroll a pane to show something, in pixels from the top of what it scrolls. The browser stops at the
 * ends, so near the top or bottom of a document "the middle" is as close as it can get. */

/** The scroll that puts a thing (top, height) in the middle of a view `view` pixels tall. */
export function centered(top: number, height: number, view: number): number {
  return Math.max(0, top - (view - height) / 2);
}

/** The scroll that keeps a thing in view with as little movement as possible (none when it already is). */
export function keptInView(top: number, height: number, scroll: number, view: number): number {
  if (top < scroll) return top;
  if (top + height > scroll + view) return Math.min(top, top + height - view);
  return scroll;
}
