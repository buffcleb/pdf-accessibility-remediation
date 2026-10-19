/** A formula whose two readings disagree (#403): the AI's, and MathCAT's read by rule from the formula's LaTeX. The
 * review finding carries both; Edit tag shows them with the formula and takes the person's choice, which goes with
 * the edit so the engine's report records it. Kept apart from the form so it can be tested without a browser. */

export const DISAGREE = "two readings of a formula disagree";

type Difference = { ai_only: string[]; rule_only: string[] };
export type Reading = { source: "ai" | "rule" | "picture"; label: string; text: string };
export type Readings = {
  formula: string; latex?: string; mathml?: string | null; picture_mathml?: string | null; readings: Reading[];
  differs?: { numbers?: Difference; letters?: Difference } | null;
  differs_picture?: { numbers?: Difference; letters?: Difference } | null;  // against the formula reader's (#403)
};
export type ReadingChoice = {
  formula: string; chose: "ai" | "rule" | "picture" | "own"; readings: { ai: string; rule: string; picture?: string };
};

/** The two readings from an element's findings, or null when none disagree. */
export function readingsOf(findings: { issue: string; extra?: unknown }[]): Readings | null {
  const f = findings.find((x) => x.issue === DISAGREE);
  const extra = f?.extra as Readings | undefined;
  if (!extra || !Array.isArray(extra.readings) || extra.readings.length < 2) return null;
  return extra;
}

/** Which reading the text is: one of the two as it stands, or the person's own words. */
export function chosen(text: string, r: Readings): ReadingChoice["chose"] {
  const t = text.trim();
  return r.readings.find((x) => x.text.trim() === t)?.source ?? "own";
}

/** What goes with the edit: which formula, which reading, and both readings (shortened to keep the edit small). */
export function choiceFor(text: string, r: Readings): ReadingChoice {
  const of = (s: Reading["source"]) => (r.readings.find((x) => x.source === s)?.text ?? "").slice(0, 1200);
  const picture = r.readings.some((x) => x.source === "picture") ? { picture: of("picture") } : {};
  return { formula: r.formula, chose: chosen(text, r), readings: { ai: of("ai"), rule: of("rule"), ...picture } };
}

/** What they say differently, in words: "numbers: the AI has 3, by rule 2; letters: the AI has b, by rule c", and
 * against the picture's reading, "the AI's transcription has 3, the picture 2". */
export function differences(r: Readings): string {
  const part = (name: string, d: Difference | undefined, mine: string, theirs: string) =>
    d && (d.ai_only.length || d.rule_only.length)
      ? `${name}: ${mine} ${d.ai_only.join(", ") || "none"}, ${theirs} ${d.rule_only.join(", ") || "none"}` : "";
  return [
    part("numbers", r.differs?.numbers ?? undefined, "the AI has", "by rule"),
    part("letters", r.differs?.letters ?? undefined, "the AI has", "by rule"),
    part("numbers", r.differs_picture?.numbers ?? undefined, "the AI's transcription has", "the picture"),
    part("letters", r.differs_picture?.letters ?? undefined, "the AI's transcription has", "the picture"),
  ].filter(Boolean).join("; ");
}
