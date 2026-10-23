// The report chart's text (#545): how wide its end labels are, and a name shortened to fit. Run the tests with: npm test

export const CHAR_W = 6.6;  // an average character of the 12px end labels

/** A line's name as its end label: shortened with "…" to `fits` characters. */
export function endName(name: string, fits: number): string {
  return name.length <= fits ? name : `${name.slice(0, Math.max(1, fits - 1)).trimEnd()}…`;
}
