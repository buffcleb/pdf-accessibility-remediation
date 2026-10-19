import DOMPurify from "dompurify";
import { marked } from "marked";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useSeen } from "../components/seen";
import { MARK_LABELS, download, get, getText, heldFor, post, put, type DocVersion, type Finding, type FindingMark, type FindingMarks, type Job, type JobFile, type MarkStatus, type RemovalResult, type Versions } from "../api";
import { parseVeraPdf, VeraPdfView, type VeraReport } from "../components/verapdf";
import { RunDetails } from "../components/RunDetails";
import { RerunDialog } from "../components/RerunDialog";
import { checkKey, checkLink } from "../checks";
import { useAuth } from "../auth";
import { HelpPanel } from "../components/help";
import { ErrorNotice, Loading, Notice, fmtBytes, fmtDate, fmtNum, isActive, useLoad, useTitle } from "../components/ui";

const SEVERITY: Record<string, [string, string]> = {
  high: ["High", "bad"],
  review: ["Review", "warn"],
  suggest: ["Suggestion", "info"],
};

const STAGES: Record<string, string> = {
  pending: "Waiting for a worker",
  started: "Starting",
  validating: "Checking the original",
  analyzing: "Reading the layout",
  enriching: "Writing alt text and reading tables",
  tagging: "Adding tags",
  verifying: "Checking the result",
  reporting: "Writing the report",
  validating_original: "Checking the original",
  ai_enrichment: "Writing alt text and reading tables",
  validating_output: "Checking the result",
  verifying_tags: "Reviewing the tags",
  adobe_sending: "Sending to Adobe",
  adobe_tagging: "Adobe is tagging the document",
  adobe_merging: "Putting Adobe's parts back together",
  waiting_daily_cap: "Waiting: today's page limit for this service has been reached",
  paused: "Waiting: support has paused this processing option for now; it will go ahead when it's resumed",
  waiting_at_once: "Waiting: this processing option is handling as many documents as it can at once",
  waiting_large: "Large document: it's processed in turn with smaller ones, so it may take longer to start",
};

// The steps shown while a document is processed, and the engine stages that belong to each
const STEPS: [string, string[]][] = [
  ["Waiting", ["", "pending", "started", "waiting_daily_cap", "paused", "waiting_large", "waiting_at_once"]],
  ["Checking the original", ["validating", "validating_original", "analyzing", "adobe_sending"]],
  ["Tagging", ["tagging", "adobe_tagging", "adobe_merging"]],
  ["Alt text and tables", ["enriching", "ai_enrichment"]],
  ["Checking the result", ["verifying", "validating_output", "verifying_tags", "reporting"]],
];

const FILE_HINTS: Record<string, string> = {
  report_pdf: "What was done, the accessibility check, and what to check.",
  primary: "Passes the check where possible. Use this one.",
  visual_fallback: "Looks identical to your original.",
  enhanced: "Adobe's file with AI alt text, font fixes and bookmarks, looking the same.",
  music_score: "The whole piece as one score, as read by software. Open it in notation software to play it, check it, or make braille music.",
  music_files: "The music one file per line or example, for notation software: play it back to check it against the page.",
  math_list: "Every formula on one page, by page. Open it in a web browser: a screen reader can move through each formula part by part.",
  math_files: "Each formula as MathML, for math software, equation editors and braille translators; the formula list is inside too.",
  math_nemeth: "Every formula in Nemeth braille, for a braille display or an embosser.",
  math_ueb: "Every formula in UEB math braille, for a braille display or an embosser.",
};

/** Pages that link here can pass this as the link's state so "Back" returns to the exact view they showed. */
export type BackState = { back?: string; backLabel?: string };

type TabKey = "review" | "report" | "check" | "staff" | "versions";

export function JobDetail() {
  const { id } = useParams();
  const location = useLocation();
  const from = (location.state as BackState | null) ?? {};
  useSeen([id ? `job:${id}` : null]);
  const { me, reloadMe } = useAuth();
  const job = useLoad(() => get<Job>(`/jobs/${id}`), [id], useCallback((j: Job) => isActive(j), []));
  const versions = useLoad(() => get<Versions>(`/jobs/${id}/versions`), [id],
    useCallback((v: Versions) => v.versions.some((x) => ["queued", "submitted", "processing"].includes(x.status)), []));
  const [params, setParams] = useSearchParams();
  const [actionError, setActionError] = useState<unknown>(null);
  const [rerunOpen, setRerunOpen] = useState(false);
  const [rerunBusy, setRerunBusy] = useState(false);
  const goTo = useNavigate();
  // Downloads and help can fold into a slim menu, giving the tabs the whole width (remembered on this browser)
  const [sideOpen, setSideOpen] = useState(() => { try { return localStorage.getItem("doc-side") !== "closed"; } catch { return true; } });
  const toggleSide = (open: boolean, focus?: string) => {
    setSideOpen(open);
    try { localStorage.setItem("doc-side", open ? "open" : "closed"); } catch { /* not kept */ }
    if (focus) window.setTimeout(() => {
      const el = document.getElementById(focus);
      el?.scrollIntoView({ block: "start", behavior: "smooth" });
      el?.focus({ preventScroll: true });
    }, 30);
  };
  useTitle(job.data?.filename ?? "Document");

  if (job.loading && !job.data) return <Loading />;
  if (job.error) return <ErrorNotice error={job.error} />;
  const j = job.data!;
  const r = j.result;
  const own = j.user_id === me?.id;
  const done = j.status === "completed" && !!r;
  const findingsTotal = r?.review?.findings_total ?? 0;
  const reviewed = (versions.data?.versions ?? []).filter((v) => v.kind === "reviewed" || v.kind === "corrected");
  const hasReport = j.files.some((f) => f.kind === "report_md");
  const hasCheck = j.files.some((f) => f.kind.startsWith("verapdf"));
  const tabs: [TabKey, string, number | null][] = done ? [
    ["review", "Needs review", findingsTotal],
    ...(hasReport ? [["report", "Report", null] as [TabKey, string, null]] : []),
    ...(hasCheck ? [["check", "Accessibility check", null] as [TabKey, string, null]] : []),
    // What the run took and where it fell short: super admins only (#313)
    ...(me?.is_super_admin ? [["staff", "Run details", null] as [TabKey, string, null]] : []),
    ...(reviewed.length ? [["versions", "Versions", reviewed.length] as [TabKey, string, number]] : []),
  ] : [];
  const asked = params.get("tab") as TabKey | null;
  const tab: TabKey = tabs.some(([k]) => k === asked) ? asked! : findingsTotal > 0 || !hasReport ? "review" : "report";
  const setTab = (t: TabKey, extra: Record<string, string | null> = {}) => {
    const next = new URLSearchParams(params);
    next.set("tab", t);
    for (const [k, v] of Object.entries(extra)) { if (v === null) next.delete(k); else next.set(k, v); }
    setParams(next, { replace: true, state: location.state });
  };

  async function getFile(fileId: string, name: string) {
    setActionError(null);
    try {
      let reason = "";
      if (!own) reason = window.prompt("You're opening someone else's file. This is recorded. Reason (optional):") ?? "";
      await download(`/jobs/${j.id}/files/${fileId}${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`, name);
    } catch (e) {
      setActionError(e);
    }
  }

  async function act(what: "confirm" | "cancel") {
    setActionError(null);
    try {
      await post(`/jobs/${j.id}/${what}`);
      await Promise.all([job.reload(), reloadMe()]);
    } catch (e) {
      setActionError(e);
    }
  }

  // A super admin's re-run (#323): a new document; open it once it's made
  async function rerun(charge: boolean, reason: string, ignoreExisting: boolean) {
    setActionError(null);
    setRerunBusy(true);
    try {
      const made = await post<Job>(`/jobs/${j.id}/rerun`, { charge, reason: reason || null, ignore_existing: ignoreExisting });
      setRerunOpen(false);
      goTo(`/jobs/${made.id}`, { state: location.state });
    } catch (e) {
      setRerunOpen(false);
      setActionError(e);
    } finally {
      setRerunBusy(false);
    }
  }
  const canRerun = !!me?.is_super_admin && !j.files_deleted && !j.deleted
    && ["completed", "failed", "canceled"].includes(j.status);

  // The document's PDFs first, then the report
  // The files people come for: the PDFs, and the music and math files when the document has music or formulas (not
  // folded away with the technical files: the people who need them are the ones checking or reading them)
  const pdfs = j.files.filter((f) => (f.name.toLowerCase().endsWith(".pdf") && f.kind !== "input")
    || ["music_files", "music_score", "math_list", "math_files", "math_nemeth", "math_ueb"].includes(f.kind))
    .sort((a, b) => Number(a.kind === "report_pdf") - Number(b.kind === "report_pdf"));
  const main = pdfs.find((f) => f.kind === "primary") ?? pdfs.find((f) => f.kind !== "report_pdf");
  const stage = (j.progress?.stage as string | undefined) ?? "";
  const fixed = Math.max(0, (r?.failed_checks_before ?? 0) - (r?.failed_checks_after ?? 0));
  const failingRules = r?.remaining_failures?.length ?? 0;

  return (
    <div className="doc-page">
      <p className="small doc-back">{from.back
        ? <Link to={from.back}>← Back to {from.backLabel ?? "the list"}</Link>
        : <Link to={own ? "/" : j.group_id ? `/admin/groups/${j.group_id}/documents` : "/admin"}>← Back</Link>}</p>

      <section className="doc-hero" aria-labelledby="doc-title">
        <div className="doc-hero-main">
          <h1 id="doc-title">{j.filename}</h1>
          <p className="doc-facts">
            {fmtNum(j.pages)} pages · submitted {fmtDate(j.created_at)}
            {j.user_email && !own ? ` by ${j.user_email}` : ""}
            {j.report_only ? " · audit only" : ""}
            {j.ai_label ? ` · ${j.ai_label}` : ""}
            {j.ignore_existing ? " · existing tags ignored" : ""}
          </p>
          <Status job={j} stage={stage} />
        </div>
        <div className="doc-hero-actions">
          {done && main && !j.files_deleted && (
            <button className="btn" onClick={() => void getFile(main.id, main.name)}>Download {main.label}</button>
          )}
          {done && <Link className="btn secondary" to={`/jobs/${j.id}/tags`}
            state={{ back: location.pathname + location.search, backLabel: "the document" } satisfies BackState}>Open in tag manager</Link>}
          {j.status === "held" && own && <>
            <button className="btn" onClick={() => void act("confirm")}>Send ({fmtNum(j.pages)} pages)</button>
            <button className="btn secondary" onClick={() => void act("cancel")}>Cancel</button>
          </>}
          {j.status === "queued" && own && <button className="btn secondary danger-text" onClick={() => void act("cancel")}>Cancel</button>}
          {canRerun && <button className="btn secondary" onClick={() => setRerunOpen(true)}>Re-run</button>}
        </div>
      </section>
      {rerunOpen && <RerunDialog job={j} busy={rerunBusy} onRun={(c, why, ignore) => void rerun(c, why, ignore)} onClose={() => setRerunOpen(false)} />}

      <ErrorNotice error={actionError} />
      {j.deleted && (
        <div className="notice" role="status">
          Deleted{j.deleted_by ? ` by ${j.deleted_by}` : ""}{j.deleted_at ? ` on ${fmtDate(j.deleted_at)}` : ""}. Its files are
          gone and it no longer appears in any document list; only super admins can open it, and reports still count it.
        </div>
      )}
      {(j.rerun_of || (j.reruns?.length ?? 0) > 0) && (
        <p className="small doc-reruns">
          {j.rerun_of && <>A re-run of <Link to={`/jobs/${j.rerun_of}`} state={location.state}>an earlier copy of this document</Link>. </>}
          {(j.reruns ?? []).length > 0 && <>Re-run as {(j.reruns ?? []).map((r, n) => (
            <span key={r}>{n > 0 ? ", " : ""}<Link to={`/jobs/${r}`} state={location.state}>{(j.reruns ?? []).length > 1 ? `copy ${n + 1}` : "a new copy"}</Link></span>
          ))}.</>}
        </p>
      )}
      <Notes job={j} />

      {done && (
        <div className="doc-tiles">
          {j.report_only ? <>
            <Tile label="Failing" value={fmtNum(r!.failed_checks_before)} unit="accessibility checks" on={tab === "check"}
              onClick={() => setTab(hasCheck ? "check" : "report")} />
            <Tile label="To review" value={fmtNum(findingsTotal)} unit="items for a person" on={tab === "review"} onClick={() => setTab("review")} />
          </> : <>
            <Tile label="Fixed" value={fmtNum(fixed)} unit="accessibility checks" on={tab === "check"}
              onClick={() => setTab(hasCheck ? "check" : "report")} />
            <Tile label="To review" value={fmtNum(findingsTotal)} unit="items for a person" on={tab === "review"} onClick={() => setTab("review")} />
            <Tile label="Still failing" value={fmtNum(failingRules)} unit={failingRules === 1 ? "rule" : "rules"}
              tone={failingRules > 0 ? "warn" : "good"} on={tab === "report" && params.get("section") === "failing"}
              onClick={() => setTab(hasReport ? "report" : "check", { section: "failing" })} />
          </>}
        </div>
      )}

      <div className="doc-columns">
        {done && (
          <section className="doc-tabs" aria-label="Document details">
            <TabList label="Document details" items={tabs.map(([k, l, n]) => ({ key: k, label: l, count: n }))} active={tab}
              onPick={(k) => setTab(k as TabKey, { section: null, rp: null })} idPrefix="doc" />
            <div role="tabpanel" id={`doc-panel-${tab}`} aria-labelledby={`doc-tab-${tab}`} className="doc-panel">
              {tab === "review" && <Review jobId={j.id} findings={r!.review?.findings ?? []} total={findingsTotal} />}
              {tab === "report" && <ReportPanel job={j} own={own} isSuper={!!me?.is_super_admin} onError={setActionError} getFile={getFile}
                onOpenReview={() => setTab("review", { section: null })} />}
              {tab === "check" && <CheckPanel job={j} own={own} isSuper={!!me?.is_super_admin} onError={setActionError} getFile={getFile} versions={reviewed} />}
              {tab === "staff" && <RunDetails job={j} />}
              {tab === "versions" && <VersionsPanel jobId={j.id} own={own} versions={reviewed} onError={setActionError}
                canEdit={!!versions.data?.can_edit} onChanged={() => void versions.reload()} />}
            </div>
          </section>
        )}

        {sideOpen || !done ? (
          <aside className="doc-side" aria-label={done ? "Downloads and help" : "Help"}>
            {/* Without results there is nothing beside this panel to make room for, so nothing to hide it for */}
            {done && <button type="button" className="linkbtn small doc-side-toggle" aria-expanded="true" onClick={() => toggleSide(false)}>
              Hide downloads and help <span aria-hidden="true">»</span></button>}
            {(done || j.files.some((f) => f.kind === "input")) && (
              <div id="doc-downloads" tabIndex={-1} aria-label="Downloads"><Downloads job={j} pdfs={pdfs} getFile={getFile} reviewed={reviewed}
                own={own} isSuper={!!me?.is_super_admin} onChanged={() => void job.reload()} onError={setActionError} /></div>
            )}
            {(done || j.status === "failed") && <div id="doc-help" tabIndex={-1} aria-label="Help"><HelpPanel job={j} /></div>}
          </aside>
        ) : (
          <nav className="doc-rail" aria-label="Downloads and help">
            <button type="button" className="doc-rail-btn" aria-expanded="false" onClick={() => toggleSide(true, "doc-downloads")}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                strokeLinejoin="round" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 21h14" /></svg>
              <span>Downloads</span>
            </button>
            <button type="button" className="doc-rail-btn" aria-expanded="false" onClick={() => toggleSide(true, "doc-help")}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17h.01" /></svg>
              <span>Help</span>
            </button>
          </nav>
        )}
      </div>
    </div>
  );
}

// --- the summary at the top -------------------------------------------------------------------------

function Status({ job: j, stage }: { job: Job; stage: string }) {
  const r = j.result;
  if (j.status === "completed" && r) {
    if (j.report_only) {
      return <div className="doc-status"><span className="doc-badge info">Audit only</span>
        <span>The file wasn't changed. Failing checks: <strong>{fmtNum(r.failed_checks_before)}</strong></span></div>;
    }
    return (
      <div className="doc-status">
        {r.compliant_after
          ? <span className="doc-badge good"><Check /> Passes PDF/UA-{r.pdfua_part ?? 1}</span>
          : <span className="doc-badge warn"><span aria-hidden="true">▲</span> Doesn't fully pass yet</span>}
        <span>Failed checks: <strong>{fmtNum(r.failed_checks_before)}</strong> → <strong>{fmtNum(r.failed_checks_after)}</strong></span>
      </div>
    );
  }
  if (isActive(j) && stage === "worker_lost") {
    // The engine's worker stopped mid-way (#315): say so, instead of the last progress it reported
    const p = (j.progress ?? {}) as Record<string, unknown>;
    return (
      <div className="doc-progress">
        <Notice kind="warn" title="The engine stopped while working on this document">
          {p.will_retry
            ? <>This often means the document needed more memory than the engine had. It will try once more, by about {fmtDate(String(p.retry_after))}. Nothing is needed from you.</>
            : <>It won't try again: the document will be marked as failed, and its pages given back.</>}
        </Notice>
      </div>
    );
  }
  if (isActive(j)) {
    const current = Math.max(0, STEPS.findIndex(([, stages]) => stages.includes(stage)));
    const queued = j.status === "queued";
    return (
      <div className="doc-progress">
        <span role="status" className="doc-progress-now">{STAGES[stage] ?? (queued ? "Waiting in the queue" : "Processing")}…</span>
        <ol aria-label="Progress" className="doc-steps">
          {STEPS.map(([label], i) => {
            const st = queued ? (i === 0 ? "now" : "todo") : i < current ? "done" : i === current ? "now" : "todo";
            return (
              <li key={label} className={st}>
                <span aria-hidden="true">{st === "done" ? "✓" : st === "now" ? "●" : "○"}</span> {label}
                <span className="visually-hidden">{st === "done" ? " (done)" : st === "now" ? " (now)" : ""}</span>
              </li>
            );
          })}
        </ol>
        <span className="small muted">This page updates by itself. You'll also get an email when it's done.</span>
      </div>
    );
  }
  if (j.status === "failed") {
    return <div className="doc-status"><span className="doc-badge bad"><span aria-hidden="true">■</span> Couldn't be processed</span>
      <span>{j.error_message} No pages were charged.</span></div>;
  }
  if (j.status === "held") {
    const why = heldFor(j);
    return <div className="doc-status"><span className="doc-badge warn">Waiting for you to confirm</span>
      <span>
        {why.includes("signed") && "This PDF is signed: the accessible copy won't carry a valid signature, so sign it again afterwards if one is needed. "}
        {why.includes("service") && `It goes to ${j.processing} only when you confirm. `}
        No pages are used until you confirm.
      </span></div>;
  }
  if (j.status === "canceled") {
    return <div className="doc-status"><span className="doc-badge muted">Canceled</span><span>No pages were charged.</span></div>;
  }
  return null;
}

function Check() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true"><path d="M5 12l4 4L19 7" /></svg>
  );
}

/** The explanations that used to be boxes: one line each, with the detail behind "Why?". */
function Notes({ job: j }: { job: Job }) {
  const r = j.result;
  const notes: { key: string; head: string; line: ReactNode; more?: ReactNode }[] = [];
  const pick = j.ai_kind_pick;
  if (pick) {
    const sure = pick.confidence != null ? ` (${Math.round(pick.confidence * 100)}% sure)` : "";
    const why: Record<string, string> = {
      unsure: `it suggested ${pick.label}${sure}, which wasn't sure enough`,
      no_text: "the first pages have no text to go on (a scan, perhaps)",
      failed: "it couldn't be reached", unavailable: "it isn't available for your group",
    };
    notes.push(pick.used
      ? { key: "pick", head: "Kind picked by AI:", line: <>{pick.label}{sure}.</>,
          more: <>You asked AI to decide the kind of document. It read the first pages' text, not the images.</> }
      : { key: "pick", head: "Kind of document:", line: <>General, because {why[pick.reason ?? ""] ?? "the AI couldn't tell"}.</>,
          more: <>You asked AI to decide the kind of document. Next time you can pick the kind yourself.</> });
  }
  if (j.ai_kind_fallback) {
    notes.push({ key: "general", head: "Processed with General:", line: <>the AI for this kind of document {j.ai_kind_fallback === "failed"
      ? "had a problem" : "wasn't available"}, so the level's General AI was used.</>, more: <>Your pages were charged as usual.</> });
  }
  if (j.ai_refunded && j.ai_unused) {
    notes.push({ key: "noai", head: "No AI needed:", line: <>no image needed alt text, so the AI level's pages weren't charged.</> });
  }
  if (j.ai_refunded && !j.ai_unused) {
    notes.push({ key: "aidown", head: "AI wasn't available:", line: <>alt text, tables and reading order came from layout rules only.</>,
      more: <>Every AI call failed. The AI level's pages weren't charged; only the processing pages were.</> });
  }
  if (r?.two_files) {
    notes.push({ key: "two", head: "Two files:", line: <>making it fully pass changed how
      {r.destructive_pages.length === 1 ? ` page ${r.destructive_pages[0]} looks`
        : r.destructive_pages.length ? ` pages ${r.destructive_pages.join(", ")} look` : " some pages look"},
      so you also get a visually exact copy.</>, more: <>
        <strong>Remediated PDF</strong> passes the check and looks slightly different where noted
        {r.fonts_embedded.length > 0 ? " (fonts replaced with metric-identical substitutes)" : ""}. <strong>Visually exact PDF</strong> looks
        identical to your original{r.visual_file_compliant === false ? ", but doesn't fully pass" : ""}.</> });
  }
  if (r?.metrics?.route === "adobe" && !j.report_only) {
    notes.push({ key: "adobe", head: "Tagged by Adobe:", line: <>Adobe doesn't write alt text, and its tags aren't guaranteed to pass.</>,
      more: <>"Adobe tagged PDF" is the file exactly as Adobe returned it{r.adobe && (r.adobe.parts ?? 1) > 1
        ? ` (sent in ${r.adobe.parts} parts and put back together)` : ""}.{j.files.some((f) => f.kind === "enhanced")
        ? ` "${r.improved_label ?? "Improved copy"}" is the same file, looking exactly the same, with the improvements; it's a separate version in the tag manager.`
        : ""} Needs review lists what to fix, and Adobe's own report is with the technical files.</> });
  }
  if (j.external_services.length > 0 && j.status !== "held") {
    // Name what went outside: the processing service (Adobe) and/or the AI level
    const parts = [j.external_services.includes(j.processing) ? j.processing : null,
      j.ai_tier && j.external_services.includes(j.ai_tier) ? (j.ai_label ?? "the AI level") : null].filter(Boolean);
    if (parts.length) {
      notes.push({ key: "ext", head: "External provider:", line: <>page images were sent to {parts.join(" and ")}, outside this service.</> });
    }
    if (j.external_services.includes("classifier")) {
      notes.push({ key: "ext-kind", head: "External provider:", line: <>the first pages' text was read by an outside service to pick the kind of document.</> });
    }
  }
  const cost = j.cost && ((j.cost.ai_usd ?? 0) > 0 || j.cost.adobe) ? <>
    {(j.cost.ai_usd ?? 0) > 0 && <>AI cost ${(j.cost.ai_usd ?? 0).toFixed(4)} over {j.cost.ai_calls ?? 0} calls{j.cost.ai_tier ? ` (${j.cost.ai_tier})` : ""}. </>}
    {j.cost.adobe && <>Adobe: {fmtNum(j.cost.adobe.pages_sent ?? 0)} pages in {j.cost.adobe.parts ?? 1} request(s), {j.cost.adobe.seconds ?? 0} s
      {j.cost.adobe.retries ? `, ${j.cost.adobe.retries} retries` : ""}.</>}</> : null;
  if (cost) notes.push({ key: "cost", head: "Cost:", line: cost });
  if (notes.length === 0) return null;
  return (
    <ul className="doc-notes">
      {notes.map((n) => (
        <li key={n.key}>
          <strong>{n.head}</strong> {n.line}
          {n.more && <details><summary>Why?</summary><span>{n.more}</span></details>}
        </li>
      ))}
    </ul>
  );
}

function Tile({ label, value, unit, on, onClick, tone }: {
  label: string; value: string; unit: string; on: boolean; onClick: () => void; tone?: "good" | "warn";
}) {
  return (
    <button type="button" className={`doc-tile${on ? " on" : ""}${tone ? ` ${tone}` : ""}`} onClick={onClick} aria-pressed={on}>
      <span className="doc-tile-label">{label}</span>
      <span className="doc-tile-value">{value}</span>
      <span className="doc-tile-unit">{unit}</span>
    </button>
  );
}

/** Accessible tabs: arrow keys, Home and End move between them; only the selected one is in the Tab order. */
function TabList({ label, items, active, onPick, idPrefix, small = false }: {
  label: string; items: { key: string; label: string; count?: number | null }[]; active: string;
  onPick: (key: string) => void; idPrefix: string; small?: boolean;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  function onKey(e: KeyboardEvent<HTMLButtonElement>, i: number) {
    const next = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0
      : e.key === "End" ? items.length - 1 : null;
    if (next === null) return;
    e.preventDefault();
    const t = items[(next + items.length) % items.length];
    onPick(t.key);
    refs.current[t.key]?.focus();
  }
  return (
    <div role="tablist" aria-label={label} className={small ? "doc-subtabs" : "doc-tablist"}>
      {items.map((t, i) => (
        <button key={t.key} ref={(el) => { refs.current[t.key] = el; }} role="tab" id={`${idPrefix}-tab-${t.key}`}
          aria-selected={t.key === active} aria-controls={`${idPrefix}-panel-${t.key}`} tabIndex={t.key === active ? 0 : -1}
          className="doc-tab" onClick={() => onPick(t.key)} onKeyDown={(e) => onKey(e, i)}>
          {t.label}{t.count != null && <span className="doc-count">{fmtNum(t.count)}</span>}
        </button>
      ))}
    </div>
  );
}

// --- downloads ----------------------------------------------------------------------------------------

/** A version's PDF. Opening someone else's file is recorded, with the reason if they give one. */
async function fetchVersionPdf(jobId: string, own: boolean, n: number, name: string, onError: (e: unknown) => void) {
  try {
    let reason = "";
    if (!own) reason = window.prompt("You're opening someone else's file. This is recorded. Reason (optional):") ?? "";
    await download(`/jobs/${jobId}/versions/${n}/pdf${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`, name);
  } catch (e) {
    onError(e);
  }
}

/** The human-reviewed file, first among the downloads: the newest by default, the others one choice away. */
function ReviewedDownload({ jobId, own, versions, onError }: {
  jobId: string; own: boolean; versions: DocVersion[]; onError: (e: unknown) => void;
}) {
  const ready = versions.filter((v) => v.status === "ready" && v.pdf_name).sort((a, b) => b.number - a.number);
  const [pick, setPick] = useState<number | null>(null);
  if (!ready.length) return null;
  const chosen = ready.find((v) => v.number === pick) ?? ready[0];
  return (
    <div className="doc-reviewed">
      <label htmlFor="dl-reviewed" className="doc-file-name">Human reviewed</label>
      {ready.length > 1 ? (
        <select id="dl-reviewed" value={chosen.number} onChange={(e) => setPick(Number(e.target.value))}>
          {ready.map((v, n) => (
            <option key={v.number} value={v.number}>{v.label}{n === 0 ? " (newest)" : ""}, {fmtDate(v.finished_at, false)}</option>
          ))}
        </select>
      ) : <span id="dl-reviewed" className="small">{chosen.label}, {fmtDate(chosen.finished_at, false)}</span>}
      <button type="button" className="btn" onClick={() => void fetchVersionPdf(jobId, own, chosen.number, chosen.pdf_name!, onError)}>
        Download{chosen.pdf_size ? ` (${fmtBytes(chosen.pdf_size)})` : ""}
      </button>
      <span className="doc-file-hint">{chosen.summary?.compliant ? `Passes PDF/UA-${chosen.summary.pdfua_part ?? 1}.` : checkLink(jobId, chosen)
        ? <Link to={checkLink(jobId, chosen)!}>{fmtNum(chosen.summary?.failed_checks)} failed checks.</Link>
        : `${fmtNum(chosen.summary?.failed_checks)} failed checks.`}{" "}
        Edited by {chosen.created_by_email ?? "–"}. All versions are under Versions.</span>
    </div>
  );
}

const STAFF_FILES = new Set(["report_md", "report_json"]);

function Downloads({ job: j, pdfs, getFile, own, isSuper, onChanged, onError, reviewed }: {
  job: Job; pdfs: JobFile[]; getFile: (id: string, name: string) => Promise<void>; own: boolean; isSuper: boolean;
  onChanged: () => void; onError: (e: unknown) => void; reviewed: DocVersion[];
}) {
  const original = j.files.find((f) => f.kind === "input");
  // The engine's own reports and veraPDF's XML are for the people who run the service (#314): everyone else has the
  // Report and Accessibility check tabs, and the PDF report
  const technical = j.files.filter((f) => !pdfs.includes(f) && f.kind !== "input"
    && (isSuper || !(STAFF_FILES.has(f.kind) || f.kind.startsWith("verapdf"))));
  return (
    <section className="doc-card" aria-labelledby="dl-h">
      <h2 id="dl-h">Downloads</h2>
      {j.files_deleted ? <p className="muted small">The files have been removed.</p> : <>
        <ReviewedDownload jobId={j.id} own={own} versions={reviewed} onError={onError} />
        {pdfs.map((f) => (
          <button key={f.id} type="button" className="doc-file" onClick={() => void getFile(f.id, f.name)}>
            <span className="doc-file-name">{f.label}</span>
            <span className="doc-file-hint">{FILE_HINTS[f.kind] ? `${FILE_HINTS[f.kind]} ` : ""}{fmtBytes(f.size_bytes)}</span>
          </button>
        ))}
        {original && <button type="button" className="linkbtn small" onClick={() => void getFile(original.id, original.name)}>
          Your original ({fmtBytes(original.size_bytes)})</button>}
        {technical.length > 0 && (
          <details className="doc-technical">
            <summary>Technical files</summary>
            <ul>
              {technical.map((f) => (
                <li key={f.id}>
                  <button type="button" className="linkbtn" onClick={() => void getFile(f.id, f.name)}>{f.label}</button>{" "}
                  <span className="muted small">{fmtBytes(f.size_bytes)}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {j.removal_at ? null : j.files_expire_at && <p className="muted small">Files are kept until {fmtDate(j.files_expire_at, false)}.</p>}
      </>}
      <Removal job={j} own={own} isSuper={isSuper} onChanged={onChanged} onError={onError} />
    </section>
  );
}

const FINISHED = ["completed", "failed", "canceled"];

/** Removing the files early: the owner marks the document done (undoable until the hold ends); super admins remove
 * files now or delete the document. */
function Removal({ job: j, own, isSuper, onChanged, onError }: {
  job: Job; own: boolean; isSuper: boolean; onChanged: () => void; onError: (e: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  if (j.deleted || !FINISHED.includes(j.status)) return null;
  const hold = j.removal_hold_hours;
  const when = hold === 0 ? "at the next cleanup (within the hour)" : `in ${hold} ${hold === 1 ? "hour" : "hours"}`;

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    onError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  function markDone() {
    if (!window.confirm(`Remove the files of “${j.filename}” ${when}?\n\nDownload anything you need first. You can undo this `
      + "until then. The document stays in your list, without its files.")) return;
    void run(() => post(`/jobs/${j.id}/remove-early`));
  }

  function adminRemove(del: boolean) {
    const what = del
      ? `Delete “${j.filename}”?\n\nIts files are removed now and it leaves every document list, including its owner's. `
        + "Reports and page usage still count it. This can't be undone."
      : `Remove the files of “${j.filename}” now?\n\nThe document stays in its owner's list, without its files. This can't be undone.`;
    if (!window.confirm(what)) return;
    const reason = window.prompt("Reason (recorded in the audit log):")?.trim();
    if (!reason || reason.length < 3) return;
    void run(() => post<RemovalResult>("/admin/jobs/remove", { ids: [j.id], delete: del, reason }).then((r) => {
      if (r.skipped.length) throw new Error(r.skipped[0].message);
    }));
  }

  return (
    <div className="doc-removal">
      {j.removal_at && !j.files_deleted && <>
        <p className="small"><strong>Marked done.</strong> The files will be removed on {fmtDate(j.removal_at)}.</p>
        {(own || isSuper) && <button type="button" className="btn secondary small" disabled={busy}
          onClick={() => void run(() => post(`/jobs/${j.id}/keep`))}>Keep the files</button>}
      </>}
      {own && !j.removal_requested && !j.files_deleted && (
        <button type="button" className="linkbtn small" disabled={busy} onClick={markDone}>
          I'm done with it: remove the files</button>
      )}
      {isSuper && (
        <details className="doc-admin-removal">
          <summary className="small">Super admin: remove</summary>
          {!j.files_deleted && <button type="button" className="btn secondary small" disabled={busy}
            onClick={() => adminRemove(false)}>Remove files now</button>}
          <button type="button" className="btn secondary small danger-text" disabled={busy}
            onClick={() => adminRemove(true)}>Delete the document</button>
        </details>
      )}
    </div>
  );
}

// --- Needs review -------------------------------------------------------------------------------------

const PAGE = 25;

function Review({ jobId, findings, total }: { jobId: string; findings: (Finding & { element?: number })[]; total: number }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const sev = params.get("sev") ?? "";
  const left = params.get("rs") === "left";  // only what hasn't been signed off
  const q = params.get("rq") ?? "";
  // Each item's status (#366): checked, fixed or not an issue, by the item's key
  const [marks, setMarks] = useState<Map<string, FindingMark>>(new Map());
  const [canMark, setCanMark] = useState(false);
  const [markError, setMarkError] = useState<unknown>(null);
  const [said, setSaid] = useState("");
  useEffect(() => {
    get<FindingMarks>(`/jobs/${jobId}/finding-marks`)
      .then((m) => { setMarks(new Map(m.marks.map((x) => [x.key, x]))); setCanMark(m.can_mark); })
      .catch(setMarkError);
  }, [jobId]);
  async function mark(f: Finding, status: MarkStatus | null, note?: string | null) {
    if (!f.key) return;
    try {
      const m = await put<FindingMark | null>(`/jobs/${jobId}/finding-marks/${f.key}`,
        { status, note: note === undefined ? marks.get(f.key)?.note ?? null : note });
      setMarks((cur) => { const next = new Map(cur); if (m) next.set(f.key!, m); else next.delete(f.key!); return next; });
      setSaid(status ? `${f.issue}: ${MARK_LABELS[status]}.` : `${f.issue}: not looked at.`);
      setMarkError(null);
    } catch (e) { setMarkError(e); }
  }
  const signedOff = findings.filter((f) => f.key && marks.has(f.key)).length;
  const [search, setSearch] = useState(q);
  const page = Number(params.get("rp")) || 1;
  const listRef = useRef<HTMLOListElement>(null);

  const counts = useMemo(() => {
    const c: Record<string, number> = { high: 0, review: 0, suggest: 0 };
    for (const f of findings) c[f.severity] = (c[f.severity] ?? 0) + 1;
    return c;
  }, [findings]);
  const needle = q.trim().toLowerCase();
  const shown = findings.map((f, i) => ({ f, i })).filter(({ f }) => (!sev || f.severity === sev)
    && (!left || !(f.key && marks.has(f.key))) && (!needle
    || [f.issue, f.text, f.detail, f.role, f.suggested_alt, f.action].some((x) => x?.toLowerCase().includes(needle))
    || String(f.page ?? "") === needle));
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const slice = shown.slice((Math.min(page, pages) - 1) * PAGE, Math.min(page, pages) * PAGE);
  const filterable = findings.length > 8;

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (!v) next.delete(k); else next.set(k, v); }
    setParams(next, { replace: true, state: location.state });
  }
  useEffect(() => {
    if (search === q) return;
    const t = window.setTimeout(() => update({ rq: search || null, rp: null }), 300);
    return () => window.clearTimeout(t);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  // Back from the tag manager: bring the finding that was opened into view, and give it focus
  useEffect(() => {
    const m = /^#finding-(\d+)$/.exec(location.hash);
    if (!m) return;
    const t = window.setTimeout(() => {
      const el = document.getElementById(`finding-${m[1]}`);
      if (!el) return;
      el.scrollIntoView({ block: "center" });
      el.focus({ preventScroll: true });
      el.classList.add("flash");
      window.setTimeout(() => el.classList.remove("flash"), 1600);
    }, 60);
    return () => window.clearTimeout(t);
  }, [location.hash, slice.length]);

  function show(i: number, element: number) {
    // Remember the place (this view, scrolled to this finding) so Back returns to it, then open the viewer
    navigate(`${location.pathname}${location.search}#finding-${i}`, { replace: true, state: location.state });
    navigate(`/jobs/${jobId}/tags?v=1&el=${element}`, {
      state: { back: `${location.pathname}${location.search}#finding-${i}`, backLabel: "the document" } satisfies BackState,
    });
  }

  return (
    <div className="review">
      <p className="muted small" style={{ margin: 0 }}>Things a machine can't be sure about. Check high and review items before
        publishing. AI-written alt text is a draft.</p>
      {findings.length > 0 && (
        <div className="review-signoff">
          <span className="small">{fmtNum(signedOff)} of {fmtNum(findings.length)} signed off</span>
          <button type="button" className="btn secondary small" onClick={() => void download(`/jobs/${jobId}/findings.csv`, "review.csv").catch(setMarkError)}>
            Download as CSV</button>
        </div>
      )}
      {markError ? <ErrorNotice error={markError} /> : null}
      <span className="visually-hidden" aria-live="polite">{said}</span>
      {findings.length === 0 ? <p className="empty">Nothing flagged.</p> : <>
        {filterable && (
          <div className="review-tools">
            <div role="group" aria-label="Severity" className="review-chips">
              {([["", "All", findings.length], ["high", "High", counts.high], ["review", "Review", counts.review],
                ["suggest", "Suggestions", counts.suggest]] as [string, string, number][]).map(([k, l, n]) => (
                <button key={k || "all"} type="button" className={`review-chip${sev === k ? " on" : ""}`} aria-pressed={sev === k}
                  onClick={() => update({ sev: k || null, rp: null })}>{l} <strong>{fmtNum(n)}</strong></button>
              ))}
            </div>
            <div role="group" aria-label="Status" className="review-chips">
              <button type="button" className={`review-chip${left ? " on" : ""}`} aria-pressed={left}
                onClick={() => update({ rs: left ? null : "left", rp: null })}>Still to check <strong>{fmtNum(findings.length - signedOff)}</strong></button>
            </div>
            <label className="review-search">
              <span className="visually-hidden">Search the items</span>
              <input type="search" value={search} placeholder="Search, or a page number" onChange={(e) => setSearch(e.target.value)} />
            </label>
          </div>
        )}
        {shown.length === 0 ? <p className="muted">Nothing matches.</p> : (
          <ol className="review-list" ref={listRef} aria-label="Items to review">
            {slice.map(({ f, i }) => {
              const [label, kind] = SEVERITY[f.severity] ?? [f.severity, "muted"];
              return (
                <li key={i} id={`finding-${i}`} tabIndex={-1}>
                  <span className={`pill ${kind} review-sev`}>{label}</span>
                  <div className="review-body">
                    <span className="review-issue">{f.issue}{f.role ? <span className="muted small"> ({f.role})</span> : null}</span>
                    <span className="review-meta">
                      {[f.page != null ? <>Page {f.page}</> : null, f.text ? <q>{f.text}</q> : null, f.detail || null]
                        .filter(Boolean).map((part, k) => <span key={k}>{k > 0 ? " · " : ""}{part}</span>)}
                    </span>
                    {(f.suggested_alt || f.action) && (
                      <span className="review-action">{f.suggested_alt ? <>Suggested alt text: <q>{f.suggested_alt}</q></> : f.action}</span>
                    )}
                  </div>
                  <div className="review-side">
                    {f.element != null && (
                      <button type="button" className="btn secondary small" onClick={() => show(i, f.element!)}>
                        Show<span className="visually-hidden"> {f.issue}{f.page != null ? ` on page ${f.page}` : ""}</span>
                      </button>
                    )}
                    {f.key && <MarkControl f={f} mark={marks.get(f.key)} canMark={canMark} onMark={mark} />}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
        {shown.length > PAGE && (
          <nav className="pager" aria-label="Pages of items">
            <button type="button" className="btn secondary small" disabled={page <= 1} onClick={() => update({ rp: String(page - 1) })}>← Previous</button>
            <span className="small">{fmtNum((Math.min(page, pages) - 1) * PAGE + 1)}–{fmtNum(Math.min(shown.length, page * PAGE))} of {fmtNum(shown.length)}</span>
            <button type="button" className="btn secondary small" disabled={page >= pages} onClick={() => update({ rp: String(page + 1) })}>Next →</button>
          </nav>
        )}
        {total > findings.length && <p className="muted small">Showing {fmtNum(findings.length)} of {fmtNum(total)}. The full list is in the report.</p>}
      </>}
    </div>
  );
}

/** One review item's status: a choice for people who can edit the document, and a note once it's signed off. */
function MarkControl({ f, mark, canMark, onMark }: { f: Finding; mark?: FindingMark; canMark: boolean;
  onMark: (f: Finding, status: MarkStatus | null, note?: string | null) => void }) {
  const [note, setNote] = useState(mark?.note ?? "");
  useEffect(() => setNote(mark?.note ?? ""), [mark?.note]);
  const who = mark ? `${MARK_LABELS[mark.status]} by ${mark.set_by_email}, ${fmtDate(mark.set_at)}${mark.note ? `: ${mark.note}` : ""}` : "";
  if (!canMark) return mark ? <span className="pill good review-mark" title={who}>{MARK_LABELS[mark.status]}</span> : null;
  return (
    <div className="review-mark">
      <label>
        <span className="visually-hidden">Status of {f.issue}{f.page != null ? ` on page ${f.page}` : ""}</span>
        <select value={mark?.status ?? ""} title={who || undefined}
          onChange={(e) => onMark(f, (e.target.value || null) as MarkStatus | null)}>
          <option value="">Not looked at</option>
          {(Object.keys(MARK_LABELS) as MarkStatus[]).map((s) => <option key={s} value={s}>{MARK_LABELS[s]}</option>)}
        </select>
      </label>
      {mark && (
        <label>
          <span className="visually-hidden">Note on {f.issue}</span>
          <input type="text" value={note} maxLength={1000} placeholder="Note (optional)"
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => { if (note.trim() !== (mark.note ?? "")) onMark(f, mark.status, note.trim() || null); }} />
        </label>
      )}
    </div>
  );
}

// --- the report, in sections --------------------------------------------------------------------------

type Section = { key: string; label: string; html: string };

// The report's sections, gathered into five sub-tabs by their headings (anything unknown goes under Details)
const BUCKETS: [string, string, RegExp][] = [
  ["summary", "Summary", /^(result|summary|overview)$/i],
  ["changes", "Changes", /before and after|existing tags|change|what was done|fixes|improvements/i],
  ["failing", "Still failing", /verapdf|remaining|still fail|failures|rules/i],
  ["review", "For review", /review|findings|warnings/i],
];

/** The engine's Markdown report as HTML, in sub-tabs: Summary, Changes, Still failing, For review and Details
 * (the opening facts, metrics and anything else). Internal file links become plain text, and its Files section
 * is left out: Downloads has them. */
function reportSections(markdown: string): Section[] {
  const html = DOMPurify.sanitize(marked.parse(markdown, { async: false }) as string);
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href") ?? "";
    if (href.includes("/api/v1/jobs/")) a.replaceWith(doc.createTextNode(a.textContent ?? ""));
    else { a.setAttribute("target", "_blank"); a.setAttribute("rel", "noreferrer"); }
  });
  doc.querySelectorAll("h1").forEach((h) => h.remove());
  const raw: { label: string; html: string }[] = [{ label: "", html: "" }];
  for (const node of Array.from(doc.body.childNodes)) {
    if (node instanceof HTMLElement && node.tagName === "H2") {
      raw.push({ label: node.textContent?.trim() ?? "", html: "" });
      continue;
    }
    const div = doc.createElement("div");
    div.appendChild(node.cloneNode(true));
    raw[raw.length - 1].html += div.innerHTML;
  }
  const out: Record<string, { label: string; parts: { label: string; html: string }[] }> = {};
  for (const [key, label] of [...BUCKETS.map(([k, l]) => [k, l]), ["details", "Details"]]) out[key] = { label, parts: [] };
  for (const part of raw) {
    if (!part.html.trim() || part.label === "Files") continue;
    const key = part.label ? BUCKETS.find(([, , re]) => re.test(part.label))?.[0] ?? "details" : "details";
    // The opening facts (job, status, flags) go first in Details
    if (!part.label) { out.details.parts.unshift({ label: "About this run", html: part.html }); continue; }
    // The report's long table of items to review repeats the Needs review tab (which filters and pages it):
    // keep its summary lines, leave the table out
    if (/needs human review/i.test(part.label)) {
      const d = new DOMParser().parseFromString(part.html, "text/html");
      d.querySelectorAll("table").forEach((t) => t.remove());
      part.html = d.body.innerHTML;
    }
    out[key].parts.push(part);
  }
  const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  return Object.entries(out).filter(([, b]) => b.parts.length).map(([key, b]) => ({
    key, label: b.label,
    html: b.parts.map((p) => (b.parts.length > 1 || p.label.toLowerCase() !== b.label.toLowerCase() ? `<h3>${esc(p.label)}</h3>` : "") + p.html).join(""),
  }));
}

function ReportPanel({ job, own, isSuper, onError, getFile, onOpenReview }: {
  job: Job; own: boolean; isSuper: boolean; onError: (e: unknown) => void; getFile: (id: string, name: string) => Promise<void>;
  onOpenReview: () => void;
}) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [sections, setSections] = useState<Section[] | null>(null);
  const [loading, setLoading] = useState(false);
  // The report people download is the PDF; the Markdown only when there's no PDF (documents from before it)
  // The PDF report is the one for people; the Markdown file is super admins' (#314)
  const file = job.files.find((f) => f.kind === "report_pdf") ?? (isSuper ? job.files.find((f) => f.kind === "report_md") : undefined);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { markdown } = await get<{ markdown: string }>(`/jobs/${job.id}/report`);
      setSections(reportSections(markdown));
    } catch (e) {
      onError(e);
    } finally {
      setLoading(false);
    }
  }, [job.id, onError]);
  // Owners' reports load as they open the tab. Anyone else's open on request, because it's recorded.
  useEffect(() => { if (own && !sections && !loading && !job.files_deleted) void load(); }, [own, sections, loading, load, job.files_deleted]);

  if (job.files_deleted) return <p className="muted">The report was removed with the files.</p>;
  if (!sections) {
    return loading ? <p className="muted" role="status">Loading…</p> : !own ? (
      <div className="actions">
        <button className="btn secondary" onClick={() => void load()}>Open the report</button>
        <span className="muted small">Opening someone else's report is recorded.</span>
      </div>
    ) : null;
  }
  const asked = params.get("section");
  const current = sections.find((s) => s.key === asked) ?? sections[0];
  return (
    <div className="report-panel">
      <div className="report-panel-head">
        {sections.length > 1 && (
          <TabList label="Report sections" small idPrefix="rep" active={current.key}
            items={sections.map((s) => ({ key: s.key, label: s.label }))}
            onPick={(k) => { const n = new URLSearchParams(params); n.set("section", k); setParams(n, { replace: true, state: location.state }); }} />
        )}
        {file && <button type="button" className="linkbtn small report-download" onClick={() => void getFile(file.id, file.name)}>
          Download the report{file.kind === "report_pdf" ? " (PDF)" : ""}</button>}
      </div>
      <div role="tabpanel" id={`rep-panel-${current.key}`} aria-labelledby={`rep-tab-${current.key}`} tabIndex={0}>
        {current.key === "review" && (
          <p className="report-pointer">Every item is in <strong>Needs review</strong>, where you can filter, search and open
            each one in the tag manager. <button type="button" className="linkbtn" onClick={onOpenReview}>Open Needs review</button></p>
        )}
        <div className="report" dangerouslySetInnerHTML={{ __html: current.html }} />
      </div>
    </div>
  );
}

// --- the accessibility check (veraPDF), before and after ------------------------------------------------

const CHECKS: [string, string][] = [
  ["verapdf_before", "Before"], ["verapdf_after", "After"], ["verapdf_after_visual", "Visually exact file"],
  ["verapdf_after_enhanced", "Improved copy"],
];

function CheckPanel({ job, own, isSuper = false, onError, getFile, versions = [] }: {
  job: Job; own: boolean; isSuper?: boolean; onError: (e: unknown) => void; getFile: (id: string, name: string) => Promise<void>;
  versions?: DocVersion[]; // saved versions: each keeps its own check, shown in a tab of its own
}) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const files: { key: string; label: string; file: { id: string; name: string } }[] = [
    ...CHECKS.flatMap(([kind, label]) => {
      const f = job.files.find((x) => x.kind === kind);
      return f ? [{ key: kind, label, file: f }] : [];
    }),
    ...versions.filter((v) => v.status === "ready" && v.verapdf_file_id).map((v) => ({
      key: checkKey(v)!, label: v.label, file: { id: v.verapdf_file_id!, name: `${v.label} accessibility check.xml` },
    })),
  ];
  const current = files.find((x) => x.key === params.get("check")) ?? files.find((x) => x.key === "verapdf_after") ?? files[0];
  // The check results are a section of the PDF report; the XML stays with the technical files
  const reportPdf = job.files.find((f) => f.kind === "report_pdf");
  const [reports, setReports] = useState<Record<string, VeraReport | null>>({});
  const [loading, setLoading] = useState<string | null>(null);
  const load = useCallback(async (key: string, fileId: string) => {
    setLoading(key);
    try {
      const xml = await getText(`/jobs/${job.id}/files/${fileId}`);
      setReports((r) => ({ ...r, [key]: parseVeraPdf(xml) }));
    } catch (e) {
      onError(e);
    } finally {
      setLoading(null);
    }
  }, [job.id, onError]);
  useEffect(() => {
    if (own && current && !(current.key in reports) && loading === null && !job.files_deleted) void load(current.key, current.file.id);
  }, [own, current, reports, loading, load, job.files_deleted]);

  if (job.files_deleted) return <p className="muted">The check results were removed with the files.</p>;
  if (!current) return null;
  const report = reports[current.key];
  const isVersion = versions.some((v) => checkKey(v) === current.key);
  return (
    <div className="report-panel">
      <p className="muted small" style={{ margin: 0 }}>The PDF/UA check (veraPDF: PDF/UA-1, or PDF/UA-2 for a PDF 2.0 document) on your original, on the result, and on each version saved from the tag manager.</p>
      {files.length > 1 && (
        <TabList label="Which file" small idPrefix="chk" active={current.key} items={files.map((x) => ({ key: x.key, label: x.label }))}
          onPick={(k) => { const n = new URLSearchParams(params); n.set("check", k); setParams(n, { replace: true, state: location.state }); }} />
      )}
      <div role="tabpanel" id={`chk-panel-${current.key}`} aria-labelledby={`chk-tab-${current.key}`} tabIndex={0}>
        {loading === current.key && <p className="muted" role="status">Loading…</p>}
        {!(current.key in reports) && loading !== current.key && !own && (
          <div className="actions">
            <button className="btn secondary" onClick={() => void load(current.key, current.file.id)}>Open the check results</button>
            <span className="muted small">Opening someone else's results is recorded.</span>
          </div>
        )}
        {current.key in reports && (report ? (
          // The PDF report covers the engine's run; a saved version's check is its own XML
          // veraPDF's XML is super admins' (#314); everyone gets the PDF report where there is one
          <VeraPdfView report={report}
            downloadLabel={reportPdf && !isVersion ? "Download the report (PDF)" : "Download XML"}
            onDownload={reportPdf && !isVersion ? () => void getFile(reportPdf.id, reportPdf.name)
              : isSuper ? () => void getFile(current.file.id, current.file.name) : undefined} />
        ) : (
          <Notice kind="warn">These results couldn't be read.{isSuper && <> <button className="linkbtn" onClick={() => void getFile(current.file.id, current.file.name)}>Download the XML</button></>}</Notice>
        ))}
      </div>
    </div>
  );
}

// --- saved versions -------------------------------------------------------------------------------------

function VersionsPanel({ jobId, own, versions, onError, canEdit, onChanged }: {
  jobId: string; own: boolean; versions: Versions["versions"]; onError: (e: unknown) => void;
  canEdit: boolean; onChanged: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  async function undo(v: DocVersion) {
    if (!window.confirm(`Undo the last save into ${v.label}? It goes back to the file it had before, and the newer file is removed.`)) return;
    setBusy(v.number);
    try {
      await post(`/jobs/${jobId}/versions/${v.number}/undo-save`);
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
    }
  }
  return (
    <ol className="versions-list">
      {versions.map((v) => (
        <li key={v.number}>
          <span className="versions-name">{v.status === "ready" && v.pdf_name
            ? <button className="linkbtn" onClick={() => void fetchVersionPdf(jobId, own, v.number, v.pdf_name!, onError)}>{v.label}</button> : <strong>{v.label}</strong>}</span>
          <span className="muted small">
            {v.status === "ready"
              ? <>{v.summary?.compliant ? `passes PDF/UA-${v.summary.pdfua_part ?? 1}` : checkLink(jobId, v)
                ? <Link to={checkLink(jobId, v)!}>{fmtNum(v.summary?.failed_checks)} failed checks</Link>
                : `${fmtNum(v.summary?.failed_checks)} failed checks`} · {v.edits_count} edit(s) by {v.created_by_email ?? "–"}, {fmtDate(v.finished_at)}</>
              : v.status === "failed" ? `not saved: ${v.error_message ?? ""}`
              : v.replacing != null ? "being checked; it replaces the one above when it passes…" : "being checked…"}
          </span>
          {v.note && <span className="muted small">“{v.note}”</span>}
          {v.undo_until && (
            <span className="muted small">Saved into this version. The file it had before is kept until {fmtDate(v.undo_until)}.{" "}
              {canEdit && <button className="linkbtn" disabled={busy === v.number} onClick={() => void undo(v)}>
                {busy === v.number ? "Undoing…" : "Undo this save"}</button>}</span>
          )}
        </li>
      ))}
    </ol>
  );
}
