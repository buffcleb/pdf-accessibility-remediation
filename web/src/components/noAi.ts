import type { ServiceChoice } from "../api";

/** No AI as a choice for remediation (#659): the AI level's key the server takes for it. */
export const NO_AI = "none";
export const NO_AI_LABEL = "No AI";

export type NoAiSetting = "off" | "on" | "default";

/** No AI as one more AI level in the upload form's pickers: nothing sent to any AI, pages from the processing
 * service only, figures left for a person to describe. */
export function noAiChoice(preselected: boolean): ServiceChoice {
  return {
    key: NO_AI, kind: "ai_tier", label: NO_AI_LABEL,
    description: "Nothing is sent to an AI. Uses pages of the processing service only. Figures are left without alt "
      + "text, each listed for you to describe in the tag manager.",
    external: false, notice: null, confirm_before_send: false, max_pages_per_job: null, remaining_pages: null,
    is_default: preselected, allow_full_review: false, ai_use: null, kinds: [], audit_ai: "off",
  };
}

/** The AI levels the upload form offers: the group's, then No AI when the group offers it. When No AI is preselected
 * it's the default instead of the group's default level. */
export function withNoAi(levels: ServiceChoice[], setting: NoAiSetting | undefined): ServiceChoice[] {
  if (!setting || setting === "off") return levels;
  const pre = setting === "default";
  return [...(pre ? levels.map((l) => ({ ...l, is_default: false })) : levels), noAiChoice(pre)];
}

/** What the document page says under the verdict for a document made with No AI: the figures left for a person. */
export function noAiLine(missing: number | null | undefined): string {
  if (!missing) return "Made without AI.";
  return `Made without AI: ${missing === 1 ? "1 figure has" : `${missing.toLocaleString("en-US")} figures have`} no alt `
    + "text. Each is listed under To review for you to describe in the tag manager.";
}
