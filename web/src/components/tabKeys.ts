/** The tab a key moves to in a row of `count` tabs, from the one at `at`: arrows go round, Home and End go to the
 * first and last. Null for any other key. */
export function tabStep(key: string, at: number, count: number): number | null {
  const next = key === "ArrowRight" ? at + 1 : key === "ArrowLeft" ? at - 1 : key === "Home" ? 0
    : key === "End" ? count - 1 : null;
  return next === null || count === 0 ? null : (next + count) % count;
}
