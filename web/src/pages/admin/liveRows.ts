/** The live view of the queues: what a row says about where an item is, and for how long. Kept apart from the
 * page so it can be tested without a browser. */
export type LiveItem = {
  key: string; kind: "documents" | "edits" | "reviews" | "mail"; id: string; job_id: string | null; title: string;
  who: string; group_id: string | null; group: string | null; queue: string; pages: number | null;
  state: "active" | "waiting" | "done" | "failed" | "canceled"; stage: string | null; percent: number | null;
  detail: string | null; created_at: string; started_at: string | null; finished_at: string | null;
  can_retry: boolean; can_cancel: boolean; can_stop: boolean; can_restart?: boolean; can_remove: boolean; can_process_now: boolean;
};
export type Live = { items: LiveItem[]; counts: Record<string, number>; more: boolean; recent_minutes: number };

export const LIVE_STATES: [string, string][] = [["active", "In progress"], ["waiting", "Waiting"], ["done", "Finished"],
  ["failed", "Failed"], ["canceled", "Canceled"]];

/** The engine's stages in an admin's words. An unknown stage is shown as the engine names it: a new stage should
 * be visible here before anyone has written a label for it. */
const STAGES: Record<string, string> = {
  pending: "Starting", started: "Starting",
  validating_original: "Checking the original", analyzing: "Reading the layout",
  ai_ocr: "AI: correcting the text of scanned pages", ai_enrichment: "AI: alt text, tables and notation",
  tagging: "Adding tags", ai_existing_figures: "AI: describing figures already tagged",
  validating_output: "Checking the result", destructive_fixes: "Fixing what the check found",
  flattening: "Flattening pages", reporting: "Writing the report", pdfua2: "Making the PDF/UA-2 file", verifying_tags: "AI: reviewing the tags",
  adobe_sending: "Sending to Adobe", adobe_tagging: "Adobe is tagging", adobe_merging: "Putting Adobe's parts together",
  worker_lost: "Worker lost: waiting to be retried",
};

export function stageLabel(item: Pick<LiveItem, "state" | "stage" | "kind">): string {
  if (item.state !== "active") return "";
  if (item.stage) return STAGES[item.stage] ?? item.stage.replace(/_/g, " ");
  return item.kind === "reviews" ? "AI: reviewing the tags" : item.kind === "edits" ? "Applying the edit" : "Starting";
}

/** A length of time, short: "40 s", "12 min", "3 h 5 min". Seconds matter here: the view is of what is moving. */
export function span(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.floor(m / 60)} h`;
}

/** How long an item has been where it is: running (since it started), waiting (since it was sent), or ended. */
export function howLong(item: Pick<LiveItem, "state" | "created_at" | "started_at" | "finished_at">, now: number): string {
  const at = (v: string | null) => (v ? new Date(v).getTime() : null);
  if (item.state === "active") return `for ${span(now - (at(item.started_at) ?? at(item.created_at)!))}`;
  if (item.state === "waiting") return `for ${span(now - at(item.created_at)!)}`;
  const end = at(item.finished_at);
  if (end === null) return "";
  const took = at(item.started_at) !== null ? `, took ${span(end - at(item.started_at)!)}` : "";
  return `${span(now - end)} ago${took}`;
}

/** The request for the list, from what the address says. */
export function liveQuery(params: URLSearchParams): string {
  const api = new URLSearchParams();
  for (const [from, to] of [["q", "q"], ["group", "group_id"], ["in", "queue"], ["state", "state"]]) {
    const v = params.get(from);
    if (v) api.set(to, v);
  }
  return api.toString();
}
