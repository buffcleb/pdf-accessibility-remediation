/** Where a version's accessibility check (veraPDF) is shown: its tab on the document page's Accessibility check. The
 * engine's own results have a tab each; every saved version (human reviewed, corrected) has one of its own. */
import type { DocVersion } from "./api";

const ENGINE: Partial<Record<DocVersion["kind"], string>> = {
  original: "verapdf_before", primary: "verapdf_after", visual_fallback: "verapdf_after_visual", enhanced: "verapdf_after_enhanced",
};

/** The tab's key, or null when the version has no check of its own to show. */
export function checkKey(v: Pick<DocVersion, "kind" | "number" | "verapdf_file_id">): string | null {
  if (ENGINE[v.kind]) return ENGINE[v.kind]!;
  return v.verapdf_file_id ? `v${v.number}` : null;
}

/** The link from anywhere to that tab. */
export function checkLink(jobId: string, v: Pick<DocVersion, "kind" | "number" | "verapdf_file_id">): string | null {
  const key = checkKey(v);
  return key ? `/jobs/${jobId}?tab=check&check=${encodeURIComponent(key)}` : null;
}
