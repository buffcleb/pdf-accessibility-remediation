/** Where "Report a problem" goes from the tag manager: the document's help form, already saying which version is
 * shown and, when a tag is selected, which tag and page. With no tag selected it is about the document (or the page
 * in view), so the button is always there: it used to appear only once a tag was selected, which read as missing. */
export type ReportTag = { i: number; role: string; page?: number | null; alt?: string | null; text?: string | null };

export function reportLink(jobId: string, version: number, tag: ReportTag | null, pageInView: number | null): string {
  const q = new URLSearchParams({ help: "1", version: String(version) });
  const page = tag ? tag.page : pageInView;
  if (page) q.set("page", String(page));
  if (tag) q.set("tag", JSON.stringify({ i: tag.i, role: tag.role, text: (tag.alt ?? tag.text ?? "").slice(0, 120) }));
  return `/jobs/${jobId}?${q.toString()}`;
}

export function reportLabel(tag: ReportTag | null): string {
  return tag ? "Report a problem here" : "Report a problem";
}
