/** Which chosen AI levels don't offer Check only (their audits set to "disabled", #586), so the upload form can say
 * so and not offer it. tiers: the batch's level and each file's own; null where none is chosen. */
export function checkNotOffered(
  levels: { key: string; label: string; audit_ai: string | null }[], tiers: (string | null | undefined)[],
): string[] {
  const off = new Set<string>();
  for (const t of tiers) {
    const l = t ? levels.find((x) => x.key === t) : undefined;
    if (l?.audit_ai === "disabled") off.add(l.label);
  }
  return [...off];
}

/** The line under the buttons when Check only isn't offered. */
export function checkNotOfferedLine(labels: string[]): string {
  const names = labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
  return `Check only isn't offered with ${names}. To check, choose another AI level.`;
}
