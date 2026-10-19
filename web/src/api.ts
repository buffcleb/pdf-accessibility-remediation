// API client. The access token lives only in memory; the refresh token is an HttpOnly cookie the
// page never sees. A 401 triggers one refresh attempt, then the user is sent to sign in.
import type { ReadingChoice, Readings } from "./viewer/readings";

export type Role = { role: "super_admin" | "group_admin"; group_id: string | null };

export type User = {
  id: string;
  email: string;
  name: string | null;
  removed_at?: string | null; // their data removed on request (#338)
  group_id: string | null;
  group_name: string | null;
  status: "active" | "disabled";
  /** Waiting for a group admin's approval, approved, or declined; separate from the account status. */
  approval: "pending" | "approved" | "declined";
  approval_at: string | null;
  approval_by: string | null;
  decline_reason: string | null;
  auth_method: string;
  roles: Role[];
  cap_pages: number | null;
  effective_cap: number | null;
  used_pages: number;
  reserved_pages: number;
  remaining_pages: number | null;
  services: UserService[];
  created_at: string;
  last_login_at: string | null;
  /** "Describe with AI" set for this person; null follows their group. */
  ai_describe?: boolean | null;
};

/** A user's access to one service. */
export type UserService = {
  key: string;
  label: string;
  kind: "processing" | "ai_tier";
  granted: boolean;
  granted_individually: boolean;
  available: boolean;
  cap_pages: number | null;
  effective_cap: number | null;
  used_pages: number;
  reserved_pages: number;
  remaining_pages: number | null;
};

/** A group's terms for one service. */
export type GroupService = {
  key: string;
  label: string;
  kind: "processing" | "ai_tier";
  globally_enabled: boolean;
  external: boolean;
  metered: boolean;
  enabled: boolean;
  available: boolean;
  all_users: boolean;
  is_default: boolean;
  default_user_cap: number | null;
  balance: Balance;
  /** Active members who can use it (everyone, or those given it). */
  people_with_access: number;
  /** Active members with a limit of their own. */
  own_limits: number;
  /** AI levels: kinds of document beyond General (never models). */
  kinds: { key: string; label: string }[];
};

/** A service as super admins see and configure it. */
export type Service = {
  key: string;
  kind: "processing" | "ai_tier";
  label: string;
  description: string | null;
  enabled: boolean;
  external: boolean;
  notice_enabled: boolean;
  notice_text: string | null;
  metered: boolean;
  global_limited: boolean;
  pool_pages: number | null;
  used_pages: number;
  reserved_pages: number;
  remaining_pages: number | null;
  daily_cap_pages: number | null;
  /** Documents processed at once with this service; null: no limit of its own. */
  max_at_once: number | null;
  pages_sent_today: number;
  max_pages_per_job: number | null;
  confirm_before_send: boolean;
  gateway_model: string | null;
  gateway_judge_model: string | null;
  allow_full_review: boolean;
  allow_notation: boolean;
  allow_ocr_ai: boolean;
  /** AI levels: an audit (Check only) with this level is free, counts against its pages, or is made without AI. */
  audit_ai: "free" | "charge" | "off";
  engine_route: "standard" | "adobe";
  sort: number;
  groups_enabled: number;
  /** AI levels: kinds of document beyond General, with their models (super admins only). */
  kinds: AiKind[];
};

export type AiKind = {
  key: string; label: string; description: string | null; gateway_model: string; gateway_judge_model: string | null;
  enabled: boolean; sort: number;
};

/** A service the signed-in user can pick at upload. */
export type ServiceChoice = {
  key: string;
  kind: "processing" | "ai_tier";
  label: string;
  description: string | null;
  external: boolean;
  notice: string | null;
  confirm_before_send: boolean;
  max_pages_per_job: number | null;
  remaining_pages: number | null;
  is_default: boolean;
  allow_full_review: boolean;
  /** Processing services: "full" (AI helps throughout) or "alt_text" (Adobe: optional AI alt text, in a separate copy). */
  ai_use: "full" | "alt_text" | null;
  /** AI levels: kinds of document, General first. Never model names. */
  kinds: { key: string; label: string; description: string | null }[];
  /** AI levels: an audit with this level is free, counts against its pages, or is made without AI. */
  audit_ai: "free" | "charge" | "off" | null;
};

/** What the upload form offers, as the person's group sets it. */
export type UploadOptions = {
  /** "choose": people pick the AI level and kind; "fixed": the group's are always used. */
  ai_mode: "choose" | "fixed";
  ai_level: string | null;
  ai_kind: string | null;
  /** Mark the options that send documents to an outside provider. */
  show_external: boolean;
};

/** A file's own AI level and kind, instead of the batch's. */
export type FileChoice = { ai_tier: string | null; ai_kind: string | null };

export type Me = User & {
  is_super_admin: boolean;
  admin_of: string[];
  /** Approved, or an admin. */
  can_submit: boolean;
  /** People waiting for approval in the groups this person administers. */
  pending_approvals: number;
  /** Their own help requests with something new. */
  help_unread: number;
  /** Super admins: help requests waiting on a super admin. */
  help_needs_us: number;
  /** "Describe with AI" in the tag manager, as the service default, the group and the person work out. */
  can_describe?: boolean;
};

export type HelpKind = "alt_text" | "reading_order" | "tables" | "headings" | "math" | "visual" | "other" | "failed";
export type HelpStatus = "open" | "in_progress" | "waiting" | "resolved" | "closed";
export type HelpAttachment = { id: string; name: string; content_type: string; size_bytes: number };
export type HelpMessage = {
  id: string;
  /** "note": super admins only; "event": a status change or hand-over. */
  kind: "message" | "note" | "event";
  author_email: string;
  author_role: "person" | "group_admin" | "super_admin" | "system";
  body: string;
  created_at: string;
  attachments: HelpAttachment[];
};
export type HelpSummary = {
  id: string;
  job_id: string | null;
  filename: string;
  user_email: string;
  group_id: string | null;
  group_name: string | null;
  kind: HelpKind;
  status: HelpStatus;
  version_number: number | null;
  version_label: string | null;
  page: number | null;
  processing: string | null;
  ai_tier: string | null;
  assignee_email: string | null;
  saved: boolean;
  needs_staff: boolean;
  unread: boolean;
  created_at: string;
  updated_at: string;
  waiting_since: string;
};
export type HelpRequest = HelpSummary & {
  tag: { i: number; role: string; text: string } | null;
  role: "person" | "group_admin" | "super_admin";
  messages: HelpMessage[];
  /** What was done, as the person was told. */
  resolution: string | null;
  resolved_version_number: number | null;
  resolved_version_label: string | null;
  resolved_version_status: string | null;
  pages_refunded: number | null;
  finished_at: string | null;
  /** The person can reopen it themselves until then. */
  reopen_until: string | null;
  can_reopen: boolean;
  /** What the document cost (super admins), as a hint for giving pages back. */
  pages_charged: number | null;
};

export type Balance = { pool_pages: number; used_pages: number; reserved_pages: number; remaining_pages: number };

export type Group = {
  id: string;
  name: string;
  status: "active" | "disabled";
  domains: string[];
  /** People allowed in by their address, without their domain (super admins change them). */
  emails: { email: string; note: string | null; added_by: string | null; created_at: string; has_account: boolean }[];
  local_only: boolean;
  require_approval: boolean;
  /** "choose": people pick the AI level and kind; "fixed": the group's level and kind are always used. */
  ai_mode: "choose" | "fixed";
  ai_level: string | null;
  ai_kind: string | null;
  /** Mark the upload options that send documents to an outside provider. */
  show_external: boolean;
  /** "Describe with AI" for the group's people; null follows the service default. */
  ai_describe?: boolean | null;
  pending_members: number;
  help_active: number;
  default_user_cap: number | null;
  over_quota_message: string | null;
  balance: Balance;
  services: GroupService[];
  created_at: string;
};

export type Finding = {
  layer?: string;
  severity: "high" | "review" | "suggest" | string;
  issue: string;
  page?: number;
  role?: string;
  text?: string;
  detail?: string;
  action?: string;
  confidence?: number;
  suggested_alt?: string;
  key?: string; // what the item is about, for its status (#366); missing on findings a client made itself
};

/** A review item signed off on a document (#366), kept by the item's key so it stays with it in later versions. */
export type MarkStatus = "checked" | "fixed" | "not_issue";
export type FindingMark = { key: string; status: MarkStatus; note: string | null; set_by_email: string; set_at: string };
export type FindingMarks = { can_mark: boolean; marks: FindingMark[] };
export const MARK_LABELS: Record<MarkStatus, string> = { checked: "Checked", fixed: "Fixed", not_issue: "Not an issue" };

export type JobResult = {
  pdfua_part?: 1 | 2;  // which PDF/UA the verdicts are by: 2 for a PDF 2.0 document (#409)
  compliant_before: boolean | null;
  compliant_after: boolean | null;
  failed_checks_before: number | null;
  failed_checks_after: number | null;
  visual_file_compliant: boolean | null;
  two_files: boolean;
  destructive_pages: number[];
  fonts_embedded: string[];
  remaining_failures: { clause: string; description: string; failed_checks: number; pages: number[] }[];
  before_after: Record<string, [unknown, unknown]>;
  review: {
    integrity_passed: boolean | null;
    summary: Record<string, number>;
    ai: Record<string, unknown>;
    findings: Finding[];
    findings_total: number;
  };
  summary: string;
  warnings: string[];
  metrics: Record<string, unknown>;
  /** Adobe route only: the improved copy's name and what was done to it. */
  improved_label?: string | null;
  improvements?: { alt_text?: number; decorative?: number; glyph_lists?: string[]; unicode_maps?: string[]; glyph_widths?: number } | null;
  adobe?: { parts: number | null; pages_sent: number | null; seconds: number | null } | null;
};

export type JobFile = { id: string; kind: string; label: string; name: string; size_bytes: number };

export type Job = {
  id: string;
  user_id: string;
  user_email: string | null;
  group_id: string | null;
  filename: string;
  pages: number;
  pages_charged: number;
  report_only: boolean;
  processing: string;
  ai_tier: string | null;
  /** The kind of document within the AI level; null: General. */
  ai_kind: string | null;
  /** Processed with the level's General models instead: the kind's model wasn't available, or failed. */
  ai_kind_fallback: "unavailable" | "failed" | null;
  /** "Let AI decide": the classifier picks the kind before the document is sent. */
  ai_kind_auto: boolean;
  /** What it picked. used: the kind was applied (else General, for `reason`). */
  ai_kind_pick: {
    kind: string | null; label: string; confidence: number | null; used: boolean; by?: "jev" | "gateway";
    reason?: "unsure" | "no_text" | "failed" | "unavailable" | "not_offered" | "no_answer";
  } | null;
  /** The level and kind by name, e.g. "Premium AI · Math and science". */
  ai_label: string | null;
  charged_services: string[];
  external_services: string[];
  needs_confirmation: boolean;
  full_review: boolean;
  ignore_existing: boolean; // its own tags were ignored and it was tagged afresh (#396)
  /** Made without AI (an audit, when its AI level is set that way). */
  no_ai: boolean;
  ai_refunded: boolean;
  ai_unused: boolean;
  /** Super admins only: what the AI cost and did (writer and review split, tokens, models, refunds...). */
  cost: {
    ai_usd?: number; ai_calls?: number; ai_tier?: string | null;
    adobe?: { pages_sent?: number; parts?: number; requests?: number; retries?: number; seconds?: number };
    [key: string]: unknown;
  } | null;
  /** Super admins only (#313): the engine's job id, and when the document was sent to the engine. */
  staff?: { engine_job_id: string | null; submitted_at: string | null } | null;
  rerun_of?: string | null;  // a super admin's re-run (#323): the document it re-ran
  reruns?: string[];  // and, on the document's own page, its re-runs
  status: "held" | "queued" | "submitted" | "processing" | "completed" | "failed" | "canceled";
  progress: Record<string, unknown> | null;
  result: JobResult | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
  /** The earlier of the end of retention and, if marked done, the end of the hold. */
  files_expire_at: string | null;
  files_deleted: boolean;
  /** Marked done by its owner: the files go at removal_at; until then it can be undone. */
  removal_requested: boolean;
  removal_at: string | null;
  removal_hold_hours: number;
  /** Deleted by a super admin: only super admins still see it. */
  deleted: boolean;
  deleted_at: string | null;
  deleted_by: string | null;
  files: JobFile[];
  /** The current help request, on the owner's own list. */
  help?: { id: string; status: HelpStatus; unread: boolean } | null;
};

/** Removing documents' files early: what was done, and what was skipped and why. */
export type RemovalResult = { done: string[]; skipped: { id: string; reason: string; message: string }[] };

export type UploadResult = {
  filename: string;
  accepted: boolean;
  job: Job | null;
  error: ApiErrorBody | null;
};

export type LedgerEntry = {
  id: number;
  created_at: string;
  group_id: string | null;
  service_key: string;
  scope: "global" | "group";
  kind: "topup" | "adjust" | "reserve" | "commit" | "release" | "correct";
  pages: number;
  user_id: string | null;
  job_id: string | null;
  ticket_ref: string | null;
  ticket_url: string | null;
  override: boolean;
  comment: string | null;
  actor_email: string | null;
  pool_after: number;
  used_after: number;
  reserved_after: number;
};

export type AuditEvent = {
  id: number;
  created_at: string;
  actor_email: string | null;
  action: string;
  outcome: string;
  target_type: string | null;
  target_id: string | null;
  group_id: string | null;
  ip: string | null;
  details: Record<string, unknown>;
};

export type RuntimeSettings = {
  service_name: string;
  organization_name: string;
  logo_alt: string;
  logo_content_type: string | null;
  logo_version: number;
  contact_email: string | null;
  accent_color: string | null;
  mail_from_name: string | null;
  not_provisioned_message: string;
  account_disabled_message: string;
  over_quota_message: string;
  support_url: string | null;
  file_retention_days: number;
  record_retention_days: number;
  early_removal_hold_hours: number;
  max_file_mb: number;
  max_pages_per_document: number;
  max_files_per_upload: number;
  max_concurrent_jobs_per_user: number;
  max_queued_jobs_per_user: number;
  large_doc_pages: number;
  large_doc_every: number;
  large_doc_at_once: number;
  session_idle_hours: number;
  session_max_days: number;
  new_groups_require_approval: boolean;
  help_max_file_mb: number;
  help_max_request_mb: number;
  help_reopen_days: number;
  notify_default_documents: NotifyChoice;
  notify_default_help: NotifyChoice;
  notify_default_admin: NotifyChoice;
  email_reports_enabled: boolean;
  report_time_zone: string;
  report_send_hour: number;
  /** "Let AI decide" the kind of document: Jev (external) for groups that aren't local-only. */
  classifier_jev: boolean;
  /** A gateway model for local-only groups (and everyone when Jev is off); "" for none. */
  classifier_local_model: string;
  /** Below this confidence (0 to 1), General is used. */
  classifier_threshold: number;
  /** Pages read: the first ones and some spread through the document. */
  classifier_pages: number;
  alert_queue_minutes: number;
  alert_worker_minutes: number;
  alert_engine_minutes: number;
  alert_repeat_hours: number;
  alert_backup_hours: number;
  low_pool_percent: number;
  low_pool_pages: number;
  /** veraPDF: its running service (falls back to the command line), or the command line in the engine. */
  verapdf_mode: "service" | "command_line";
  /** Also check PDF/UA-2, shown for information. */
  verapdf_ua2: boolean;
  /** A document that fails gets a help request of its own. */
  help_on_failure: boolean;
  /** "Describe with AI" in the tag manager, for everyone unless their group or they are set otherwise. */
  ai_describe_default: boolean;
};

export type EmailReportChoice = { kind: string; label: string; description: string; frequency: "monthly" | "weekly" | "daily" | "off" };
export type NotifyChoice = "each" | "daily" | "off";
export type NotificationChoice = {
  category: string; label: string; description: string; choice: NotifyChoice;
  /** Email even about what they saw in the portal; null where it doesn't apply. */
  even_if_seen: boolean | null;
};
export type Notifications = { choices: NotificationChoice[] };
export type ReportView = {
  id: string; name: string; report: string; report_label: string; params: Record<string, string>; group_id: string | null;
  frequency: "monthly" | "weekly" | "daily"; attach_csv: boolean; owner: string; mine: boolean;
  recipients: { id: string; email: string }[]; link: string;
};
export type EmailReports = { enabled: boolean; time_zone: string; send_hour: number; reports: EmailReportChoice[] };

export type ApiErrorBody = {
  error: string;
  message: string;
  fields?: { field: string; message: string }[];
  support_url?: string | null;
  scope?: string;
  remaining_pages?: number;
  service?: string;
};

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody;
  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.body = body;
  }
}

type TokenResponse = { access_token: string; expires_in: number; user: Me };

let apiBase = "";
let accessToken: string | null = null;
let refreshTimer: number | undefined;
let refreshing: Promise<Me | null> | null = null;
let onSignedOut: () => void = () => {};

export function configureApi(base: string, signedOut: () => void) {
  apiBase = base.replace(/\/$/, "");
  onSignedOut = signedOut;
}

function url(path: string) {
  return `${apiBase}/api/v1${path}`;
}

async function parseError(r: Response): Promise<ApiError> {
  let body: ApiErrorBody = { error: "http_" + r.status, message: "Something went wrong. Try again." };
  try {
    const j = await r.json();
    if (j && typeof j.detail === "object" && j.detail) body = j.detail;
    else if (j && typeof j.detail === "string") body = { error: "error", message: j.detail };
  } catch {
    /* not JSON */
  }
  return new ApiError(r.status, body);
}

function acceptTokens(t: TokenResponse): Me {
  accessToken = t.access_token;
  window.clearTimeout(refreshTimer);
  // Refresh a minute before the access token expires
  refreshTimer = window.setTimeout(() => void refreshSession(), Math.max(10, t.expires_in - 60) * 1000);
  return t.user;
}

export function clearSession() {
  accessToken = null;
  window.clearTimeout(refreshTimer);
}

/** Use the refresh cookie to get a new access token. Returns null when not signed in. */
export function refreshSession(): Promise<Me | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const r = await fetch(url("/auth/refresh"), { method: "POST", credentials: "include" });
        if (!r.ok) {
          clearSession();
          return null;
        }
        return acceptTokens(await r.json());
      } catch {
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

export async function requestCode(email: string, botCheck?: string | null) {
  const r = await fetch(url("/auth/code"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, ...(botCheck ? { bot_check: botCheck } : {}) }),
  });
  if (!r.ok) throw await parseError(r);
  return (await r.json()) as { status: string; expires_in: number };
}

export async function verifyCode(email: string, code: string): Promise<Me> {
  const r = await fetch(url("/auth/code/verify"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code }),
  });
  if (!r.ok) throw await parseError(r);
  return acceptTokens(await r.json());
}

export async function signOut(everywhere = false) {
  try {
    if (everywhere && accessToken) {
      await fetch(url("/auth/logout-all"), {
        method: "POST",
        credentials: "include",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
    } else {
      await fetch(url("/auth/logout"), { method: "POST", credentials: "include" });
    }
  } finally {
    clearSession();
  }
}

async function send(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const r = await fetch(url(path), { ...init, headers });
  if (r.status === 401 && retry) {
    const me = await refreshSession();
    if (me) return send(path, init, false);
    onSignedOut();
  }
  if (!r.ok) throw await parseError(r);
  return r;
}

export async function get<T>(path: string): Promise<T> {
  return (await send(path)).json();
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await send(path, {
    method: "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return r.status === 204 ? (undefined as T) : r.json();
}

export async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await send(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await send(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

export async function del<T>(path: string): Promise<T | undefined> {
  const r = await send(path, { method: "DELETE" });
  return r.status === 204 ? undefined : r.json();
}

export async function uploadFiles(files: File[], reportOnly: boolean, processing = "standard",
  confirm = false, aiTier: string | null = null, fullReview = false, aiAltText = true,
  aiKind: string | null = null, perFile: Record<number, FileChoice> = {}, ignoreExisting = false): Promise<UploadResult[]> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  form.append("report_only", String(reportOnly));
  form.append("processing", processing);
  form.append("ai_alt_text", String(aiAltText));
  form.append("confirm", String(confirm));
  if (aiTier) form.append("ai_tier", aiTier);
  if (aiTier && aiKind && aiKind !== "general") form.append("ai_kind", aiKind);
  if (fullReview) form.append("full_review", "true");
  if (ignoreExisting) form.append("ignore_existing", "true");
  if (Object.keys(perFile).length) form.append("per_file", JSON.stringify(perFile));
  const r = await send("/jobs", { method: "POST", body: form });
  return r.json();
}

/** POST a multipart form (fields and files). */
export async function postForm<T>(path: string, form: FormData): Promise<T> {
  const r = await send(path, { method: "POST", body: form });
  return r.json();
}

/** Fetch a file with the access token and hand it to the browser as a download. */
export async function download(path: string, fallbackName: string) {
  const r = await send(path);
  const disposition = r.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const blob = await r.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = match?.[1] ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

export async function getText(path: string): Promise<string> {
  return (await send(path)).text();
}

export async function getBytes(path: string): Promise<ArrayBuffer> {
  return (await send(path)).arrayBuffer();
}

// --- tag manager -------------------------------------------------------------------------------

export type TagBox = { page: number; bbox: [number, number, number, number] };

export type TagElement = {
  i: number;
  parent: number | null;
  kids: number[];
  role: string;
  raw_role?: string;
  page?: number;
  boxes: TagBox[];
  own: boolean;
  alt?: string;
  actual_text?: string;
  lang?: string;
  scope?: string;
  refs?: number[]; // what the tag points at (/Ref): a footnote mark's note
  span?: [number, number]; // a table cell's rows and columns, when more than one (#370)
  headers?: number[]; // a table cell's headers, as it names them (Headers): the header cells it's read with, in order
  text?: string;
  orig?: boolean; // the tag was in the document as it was uploaded (only set when its tags were kept)
  annot?: boolean; // it holds a form field, link or other annotation
  name?: string; // a form field's or link's name (its tooltip or description), when the tag has no text of its own
  say?: string; // the tag's own text in full, when it differs from text (which is cut short, and includes child tags)
  order: number | null;
  parts?: TagPart[];
};

/** One item of a tag's content, in order: a piece of content, a child tag, or a link. */
export type TagPart = { k: number; kind: "content" | "tag" | "link"; page?: number; bbox?: [number, number, number, number]; text?: string; el?: number };

/** A piece of tagged content (one marked section of a page) and the tag holding it. */
export type TagPiece = { page: number; mcid: number; el: number; bbox: [number, number, number, number]; kind: "text" | "image" | "drawing"; text?: string };
/** A section of a page marked as an artifact (not read), numbered in the page's drawing order. */
export type TagArtifact = { page: number; seq: number; bbox: [number, number, number, number]; kinds: string[]; type: string; nested?: boolean; text?: string };

export type TagMap = {
  schema: number; pages: { w: number; h: number }[]; elements: TagElement[];
  pieces?: TagPiece[]; artifacts?: TagArtifact[]; // missing from tag maps made before area editing
  document?: { title: string | null; lang: string | null }; // missing from tag maps made before #363
};

export type VersionSummary = {
  pdfua_part?: 1 | 2;
  compliant: boolean | null;
  failed_checks: number | null;
  failed_rules: number | null;
  findings: (Finding & { element?: number; extra?: { suggested_alt?: string } & Partial<Readings> })[];
  findings_total: number;
  edits_applied?: string[];
  visual_identical?: boolean;
  warnings?: string[];
};

export type DocVersion = {
  id: string;
  number: number;
  kind: "original" | "primary" | "visual_fallback" | "enhanced" | "reviewed" | "corrected";
  label: string;
  status: "queued" | "submitted" | "processing" | "ready" | "failed";
  based_on: number | null;
  created_by_email: string | null;
  note: string | null;
  edits_count: number;
  summary: VersionSummary | null;
  error_message: string | null;
  has_tagmap: boolean;
  pdf_file_id: string | null;
  pdf_name: string | null;
  pdf_size: number | null;
  verapdf_file_id: string | null;
  created_at: string;
  finished_at: string | null;
  review: "sampled" | "full" | null;
  review_status: "queued" | "running" | "done" | "failed" | null;
  reviewed_at: string | null;
  review_error: string | null;
  /** Being saved into another reviewed version: that version's number. */
  replacing?: number | null;
  /** A save into this version can be undone until then (the file before is kept a day). */
  undo_until?: string | null;
};

export type Versions = { can_edit: boolean; versions: DocVersion[] };

export type TagEdit =
  | { op: "set_role"; element: number; expect_role: string; role: string }
  // reading: which of a formula's two readings a person chose (#403); recorded in the engine's report
  | { op: "set_alt"; element: number; expect_role: string; alt: string; reading?: ReadingChoice }
  | { op: "set_actual_text"; element: number; expect_role: string; text: string; reading?: ReadingChoice }
  | { op: "set_decorative"; element: number; expect_role: string; as?: "layout" | "header" | "footer" | "page_number" }
  | { op: "set_lang"; element: number; expect_role: string; lang: string }
  | { op: "set_cell"; element: number; expect_role: string; role: "TH" | "TD"; scope?: string }
  | { op: "move"; element: number; expect_role: string; target: number; expect_target_role: string; position: "before" | "after" }
  | { op: "merge"; element: number; expect_role: string; absorb: number[] }
  | { op: "split"; element: number; expect_role: string; at: number; char?: number }
  | { op: "delete"; element: number; expect_role: string; children: "lift" | "delete" }
  | { op: "assign"; element: number; expect_role: string; pieces: { page: number; mcid: number }[] }
  | { op: "tag_artifacts"; element: number; expect_role: string; position: "before" | "after"; page: number; sections: number[]; role: string; alt?: string }
  | { op: "make_list"; element: number; expect_role: string; absorb: number[] }
  | { op: "indent"; element: number; expect_role: string }
  | { op: "outdent"; element: number; expect_role: string }
  | { op: "make_toc"; element: number; expect_role: string }
  | { op: "set_headers"; element: number; expect_role: string; headers: number[] }
  | { op: "link_note"; element: number; expect_role: string; note: number | null }
  | { op: "set_field_label"; element: number; expect_role: string; label: string }
  // The whole document's, naming no tag (#363)
  | { op: "set_doc_title"; title: string }
  | { op: "set_doc_lang"; lang: string }
  | { op: "build_list"; element: number; expect_role: string; absorb?: number[]; rows: ListRow[] }
  | { op: "take_pieces"; element: number; expect_role: string; pieces: { page: number; mcid: number }[]; role: string; alt?: string }
  | { op: "cut_area"; element: number; expect_role: string; page: number; bbox: [number, number, number, number]; role: string; alt?: string }
  // The table editor (#370): a table as it should be, or text tags made a table
  | { op: "rebuild_table"; element: number; expect_role: string; rows: TableCellSpec[][]; caption?: number | null }
  | { op: "build_table"; element: number; expect_role: string; absorb?: number[]; rows: BuildCellSpec[][] };

/** A cell of the table editor's grid, as the engine takes it: header or data, its spans, and the old cells whose
 * content it takes (a table being edited) or the pieces of text it holds (text tags being made a table). */
export type TableCellSpec = { from: number[]; role: "TH" | "TD"; scope?: "Row" | "Column" | "Both"; rows?: number; cols?: number };
export type BuildCellSpec = { parts: { el?: number; at: number }[]; role: "TH" | "TD"; scope?: "Row" | "Column" | "Both"; rows?: number; cols?: number };

/** One row of a list being built from a tag's content: where it starts (item `at` of tag `el`, the edit's own tag
 * when left out) and what it becomes: a list item at a level, or a tag of a type. */
export type ListRow = { el?: number; at: number; level?: number; as?: string };

export type MailStatus = { backend: string; from_name: string; from_address: string; reply_to: string | null };
export type MailTestResult = { sent: boolean; to: string; backend: string; error: string | null };

export async function uploadLogo(file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file, file.name);
  await send("/admin/branding/logo", { method: "PUT", body: form });
}

/** WCAG contrast ratio between two #rrggbb colors (the server checks the accent the same way). */
export function contrastRatio(a: string, b: string): number {
  const lum = (hex: string) => {
    const ch = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export type AttentionItem = {
  severity: "bad" | "warn" | "info";
  kind: string;
  title: string;
  detail: string;
  group_id?: string;
  group_name?: string;
  service_key?: string;
  count?: number;
  fix?: string;
};

export type AccessCheck = {
  user_email: string;
  service_key: string;
  service_label: string;
  can_use: boolean;
  remaining_pages: number | null;
  steps: { key: string; label: string; ok: boolean; detail: string; fix: string | null; blocking: boolean }[];
};

/** Unsaved tag edits kept on the server between sessions (one per person per document). */
export interface TagDraft { user_id: string; user_email: string; based_on: number; edits: TagEdit[]; updated_at: string }
export interface TagDraftInfo { user_id: string; user_email: string; based_on: number; edits_count: number; updated_at: string }
export interface TagDrafts { mine: TagDraft | null; others: TagDraftInfo[] }

/** Why a document waits for its owner's confirmation (#339): a service that asks for it, a signed PDF, or both. */
export function heldFor(j: Job): string[] {
  const why = j.progress?.held;
  return Array.isArray(why) ? (why as string[]) : ["service"];
}
