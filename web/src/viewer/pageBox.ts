/** The page number box in one-page view: the page that what is typed in it means. */

/** The page `text` asks for, kept within 1–`count`, or null when it isn't a whole number (the box then goes back to
 * the page being shown). */
export function typedPage(text: string, count: number): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  return Math.max(1, Math.min(count || 1, Number(t)));
}
