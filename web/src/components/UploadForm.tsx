import { useEffect, useId, useRef, useState, type DragEvent } from "react";
import {
  get, heldFor, post, uploadFiles, type ConfirmMany, type FileChoice, type Job, type ServiceChoice, type UploadOptions,
  type UploadResult,
} from "../api";
import { ErrorNotice, LiveRegion, Notice, fmtBytes, fmtNum, useLoad } from "./ui";
import { SLOW_UPLOAD_SECONDS, uploadWait } from "./uploadWait";

type Picked = { file: File; choice: FileChoice | null; open: boolean };

/** The upload form: drop PDFs, see the choices in one line each, change them for the batch or for a file,
 * then Remediate or Check only. */
export function UploadForm({ choices, onChanged }: { choices: ServiceChoice[]; onChanged: () => Promise<void> }) {
  const options = useLoad(() => get<UploadOptions>("/upload-options"), []);
  const [files, setFiles] = useState<Picked[]>([]);
  const [busy, setBusy] = useState<"" | "remediate" | "check">("");
  const [slow, setSlow] = useState(false);  // still waiting after a few seconds: say why (#560)
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [over, setOver] = useState(false);
  const [announce, setAnnounce] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [batch, setBatch] = useState<FileChoice | null>(null);
  const [changing, setChanging] = useState(false);
  const [altText, setAltText] = useState(true);
  const [fullReview, setFullReview] = useState(false);
  const [ignoreTags, setIgnoreTags] = useState(false);  // #396: the documents' own tags are poor
  const [held, setHeld] = useState<Job[]>([]);
  // Whether the held documents fit the pages left, as the server judges it (a dry run of sending them, #494)
  const [fit, setFit] = useState<ConfirmMany | null>(null);
  const heldKey = held.map((j) => j.id).join(",");
  useEffect(() => {
    if (!busy) { setSlow(false); return; }
    const t = window.setTimeout(() => setSlow(true), SLOW_UPLOAD_SECONDS * 1000);
    return () => window.clearTimeout(t);
  }, [busy]);
  useEffect(() => {
    setFit(null);
    if (held.length < 2) return;
    let live = true;
    post<ConfirmMany>("/jobs/confirm", { job_ids: held.map((j) => j.id), dry_run: true })
      .then((r) => { if (live) setFit(r); })
      .catch(() => { if (live) setFit(null); });
    return () => { live = false; };
  }, [heldKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const ids = useId();

  const opts = options.data;
  const fixed = opts?.ai_mode === "fixed";
  const showExternal = !!opts?.show_external;
  const processing = choices.filter((c) => c.kind === "processing");
  const chosen = processing.find((c) => c.key === picked) ?? processing.find((c) => c.is_default)
    ?? processing.find((c) => c.key === "standard") ?? processing[0];
  // Adobe's AI only writes alt text, and only if asked (on by default).
  const altTextOnly = chosen?.ai_use === "alt_text";
  const usesAi = !altTextOnly || altText;
  const levels = choices.filter((c) => c.kind === "ai_tier");
  const defaultLevel = levels.find((c) => c.is_default) ?? null;
  const fixedChoice: FileChoice | null = fixed ? { ai_tier: opts!.ai_level, ai_kind: opts!.ai_kind } : null;
  const base = fixedChoice ?? batch ?? { ai_tier: defaultLevel?.key ?? null, ai_kind: null };
  const level = levels.find((c) => c.key === base.ai_tier) ?? null;
  const canChange = !fixed && usesAi && (levels.length > 1 || levels.some((l) => l.kinds.length > 1));
  const fullAllowed = !level || level.allow_full_review;

  function label(c: FileChoice) {
    const l = levels.find((x) => x.key === c.ai_tier);
    if (!l) return "The service's own AI";
    const k = l.kinds.find((x) => x.key === (c.ai_kind ?? "general"));
    return k && k.key !== "general" ? `${l.label} · ${k.label}` : l.label;
  }

  async function decide(job: Job, send: boolean) {
    setError(null);
    try {
      await post(`/jobs/${job.id}/${send ? "confirm" : "cancel"}`);
      setHeld((h) => h.filter((x) => x.id !== job.id));
      setAnnounce(send ? `${job.filename} sent.` : `${job.filename} canceled.`);
      await onChanged();
    } catch (e) {
      setError(e);
    }
  }

  /** Send held documents together: all of them, or those that fit in upload order (#494). */
  async function sendMany(mode: "all" | "fit") {
    setError(null);
    try {
      const r = await post<ConfirmMany>("/jobs/confirm", { job_ids: held.map((j) => j.id), mode });
      setHeld((h) => h.filter((x) => !r.sent.includes(x.id)));
      setAnnounce(r.held.length
        ? `${r.sent.length} sent (${fmtNum(r.sent_pages)} pages). ${r.held.length} still waiting: `
          + "not enough pages left."
        : `All ${r.sent.length} sent (${fmtNum(r.sent_pages)} pages).`);
      await onChanged();
    } catch (e) {
      setError(e);
    }
  }

  function pick(list: FileList | null) {
    if (!list) return;
    setFiles(Array.from(list).map((file) => ({ file, choice: null, open: false })));
    setResults(null);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    pick(e.dataTransfer.files);
  }

  function setFile(i: number, change: Partial<Picked>) {
    setFiles((fs) => fs.map((f, j) => (j === i ? { ...f, ...change } : f)));
  }

  async function submit(check: boolean) {
    setBusy(check ? "check" : "remediate");
    setError(null);
    try {
      const perFile: Record<number, FileChoice> = {};
      if (!fixed) files.forEach((f, i) => { if (f.choice) perFile[i] = f.choice; });
      const res = await uploadFiles(files.map((f) => f.file), check, chosen?.key ?? "standard", false,
        fixed ? null : base.ai_tier, fullReview && fullAllowed && usesAi && !check,
        !altTextOnly || altText, base.ai_kind, perFile, ignoreTags && !check);
      setResults(res);
      setHeld(res.filter((r) => r.job?.status === "held").map((r) => r.job!));
      const ok = res.filter((r) => r.accepted).length;
      setAnnounce(`${ok} of ${res.length} files accepted.`);
      setFiles([]);
      if (inputRef.current) inputRef.current.value = "";
      await onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy("");
    }
  }

  const refused = results?.filter((r) => !r.accepted) ?? [];
  const accepted = results?.filter((r) => r.accepted && r.job?.status !== "held") ?? [];

  return (
    <section className="panel upload" aria-labelledby="upload-h">
      <h2 id="upload-h">Submit PDFs</h2>
      <div className={`dropzone${over ? " over" : ""}${files.length ? " has-files" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)} onDrop={onDrop}>
        <p>Drag PDFs here, or choose them.</p>
        <label className="btn secondary" htmlFor="files">Choose PDFs</label>
        <input id="files" ref={inputRef} className="visually-hidden" type="file" accept="application/pdf,.pdf" multiple
          onChange={(e) => pick(e.target.files)} />
      </div>

      {files.length > 0 && (
        <ul className="upload-files" aria-label="Files to submit">
          {files.map((f, i) => (
            <li key={`${f.file.name}-${i}`}>
              <div className="upload-file">
                <span className="upload-file-name">{f.file.name}</span>
                <span className="muted small">{fmtBytes(f.file.size)}</span>
                {usesAi && f.choice && <span className="pill info">{label(f.choice)}</span>}
                <span className="upload-file-actions">
                  {canChange && files.length > 1 && (
                    <button type="button" className="linkbtn small" aria-expanded={f.open}
                      onClick={() => setFile(i, { open: !f.open })}>
                      {f.open ? "Done" : "Change AI"}<span className="visually-hidden"> for {f.file.name}</span>
                    </button>
                  )}
                  <button type="button" className="linkbtn small"
                    onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))}>
                    Remove<span className="visually-hidden"> {f.file.name}</span>
                  </button>
                </span>
              </div>
              {f.open && (
                <div className="upload-file-choice">
                  <AiPicker id={`${ids}-f${i}`} levels={levels} showExternal={showExternal}
                    value={f.choice ?? base} onChange={(c) => setFile(i, { choice: c })} />
                  {f.choice && (
                    <button type="button" className="linkbtn small" onClick={() => setFile(i, { choice: null })}>
                      Use the same as the others
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <dl className="upload-summary">
        {processing.length > 1 && (
          <div className="upload-row">
            <dt><label htmlFor={`${ids}-proc`}>Processing</label></dt>
            <dd>
              <select id={`${ids}-proc`} value={chosen?.key ?? ""} onChange={(e) => setPicked(e.target.value)}>
                {processing.map((c) => (
                  <option key={c.key} value={c.key}>{c.label}{showExternal && c.external ? " (external)" : ""}</option>
                ))}
              </select>
              {chosen && <span className="muted small">{describe(chosen)}</span>}
            </dd>
          </div>
        )}
        {usesAi && (levels.length > 0 || fixed) && (
          <div className="upload-row">
            <dt>AI</dt>
            <dd>
              <span className="upload-ai">
                <strong>{label(base)}</strong>
                {showExternal && level?.external && <span className="pill warn">External</span>}
                {fixed && <span className="muted small">Set by your group</span>}
                {canChange && (
                  <button type="button" className="linkbtn" aria-expanded={changing}
                    aria-controls={`${ids}-ai`} onClick={() => setChanging(!changing)}>
                    {changing ? "Done" : "Change"}
                  </button>
                )}
              </span>
              {!changing && level && <span className="muted small">{describe(level)}</span>}
              {changing && (
                <div id={`${ids}-ai`} className="upload-change">
                  <AiPicker id={`${ids}-batch`} levels={levels} showExternal={showExternal} value={base}
                    onChange={setBatch} />
                  {files.length > 1 && (
                    <p className="muted small">For all {files.length} files. To give one file something else, use
                      Change AI next to it.</p>
                  )}
                </div>
              )}
            </dd>
          </div>
        )}
      </dl>

      {altTextOnly && (
        <label className="check">
          <input type="checkbox" checked={altText} onChange={(e) => setAltText(e.target.checked)} />
          <span>
            Write alt text for images with AI. <span className="muted">{chosen?.label} doesn't describe images. You get
            its file as it is, plus an improved copy with the AI alt text (and any font fixes, bookmarks and document
            fixes, which never change how the pages look). Uses pages of the AI level, given back if no image needed
            describing.</span>
          </span>
        </label>
      )}
      {chosen?.notice && <Notice kind="warn" title={`About ${chosen.label}`}>{chosen.notice}</Notice>}
      {usesAi && level?.notice && <Notice kind="warn" title={`About ${level.label}`}>{level.notice}</Notice>}

      {(() => {
        const on = (fullReview && usesAi && fullAllowed ? 1 : 0) + (ignoreTags ? 1 : 0);
        return (
          <details className="upload-more">
            <summary>More options{on ? ` (${on} on)` : ""}</summary>
            <label className="check">
              <input type="checkbox" checked={ignoreTags} onChange={(e) => setIgnoreTags(e.target.checked)} />
              <span>
                Ignore the existing tags. <span className="muted">For a PDF that is tagged, but badly: its own tags are
                set aside and it's tagged afresh from the page. Normally good tags are kept and repaired. Not used for
                Check only.</span>
              </span>
            </label>
            {usesAi && fullAllowed && (
              <label className="check">
                <input type="checkbox" checked={fullReview} onChange={(e) => setFullReview(e.target.checked)} />
                <span>
                  Review every figure with AI. <span className="muted">Normally the AI checks flagged figures plus a
                  sample; this checks them all. Slower, and uses more AI. Not used for Check only.</span>
                </span>
              </label>
            )}
          </details>
        );
      })()}

      <div className="actions upload-actions">
        <button className="btn" disabled={!!busy || files.length === 0} onClick={() => void submit(false)}>
          {busy === "remediate" ? "Uploading…" : "Remediate"}
        </button>
        <button className="btn secondary" disabled={!!busy || files.length === 0} onClick={() => void submit(true)}
          aria-describedby={`${ids}-check`}>
          {busy === "check" ? "Uploading…" : "Check only"}
        </button>
      </div>
      <p className="small upload-wait" role="status">
        {busy && slow ? uploadWait(files.length, !!opts?.malware_scan) : ""}
      </p>
      <p id={`${ids}-check`} className="muted small upload-hint">
        Remediate uses one page of your limit for each page. Check only reports problems without changing the
        files{checkCost(level, chosen)}.
      </p>

      <ErrorNotice error={error} />
      {held.length > 0 && (
        <Notice kind="warn" title={held.some((j) => heldFor(j).includes("service"))
          ? `Confirm before sending to ${chosen?.label ?? "this service"}`
          : `Confirm before sending ${held.length === 1 ? "this signed PDF" : "these signed PDFs"}`}>
          <span>Nothing is sent, and no pages are used, until you confirm each document.</span>
          {held.some((j) => heldFor(j).includes("signed")) && (
            <span>A signed PDF's accessible copy won't carry a valid signature: sign it again afterwards if one is
              needed.</span>
          )}
          <ul className="held-list">
            {held.map((j) => (
              <li key={j.id} className="actions">
                <span>{j.filename}: {fmtNum(j.pages)} page{j.pages === 1 ? "" : "s"}{heldFor(j).includes("signed") ? " (signed)" : ""}</span>
                <button className="btn small" onClick={() => void decide(j, true)}>
                  Send<span className="visually-hidden"> {j.filename}</span>
                </button>
                <button className="btn secondary small" onClick={() => void decide(j, false)}>
                  Cancel<span className="visually-hidden"> {j.filename}</span>
                </button>
              </li>
            ))}
          </ul>
          {held.length > 1 && fit && fit.held.length > 0 && (
            <span>
              <strong>These {held.length} files are {fmtNum(fit.needed_pages)} pages, and you have{" "}
                {fmtNum(fit.remaining_pages ?? 0)} {chosen?.label ?? ""} pages left.</strong>{" "}
              {fit.sent.length > 0
                ? `The first ${fit.sent.length} fit; the rest would wait here until you have more pages or cancel them.`
                : "None of them fits: cancel some, or ask for more pages."}
            </span>
          )}
          {held.length > 1 && fit && fit.held.length === 0 && (
            <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => void sendMany("all")}>
              Send all {held.length} ({fmtNum(fit.needed_pages)} pages)
            </button>
          )}
          {held.length > 1 && fit && fit.held.length > 0 && fit.sent.length > 0 && (
            <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => void sendMany("fit")}>
              Send the first {fit.sent.length} that fit ({fmtNum(fit.sent_pages)} pages)
            </button>
          )}
        </Notice>
      )}
      {accepted.length > 0 && (
        <Notice kind="good">
          {accepted.length === 1 ? `${accepted[0].filename} was accepted.` : `${accepted.length} files were accepted.`}{" "}
          You'll get an email when each one is done.
        </Notice>
      )}
      {groupRefusals(refused).map((g) => (
        <Notice key={g.message} kind={g.overQuota ? "warn" : "bad"}
          title={g.files.length === 1 ? `${g.files[0]} wasn't accepted` : `${g.files.length} files weren't accepted`}>
          <span>{g.message}</span>
          {g.files.length > 1 && <span className="small">{g.files.join(", ")}</span>}
          {g.supportUrl && <a href={g.supportUrl} target="_blank" rel="noreferrer">Request more pages</a>}
        </Notice>
      ))}
      <LiveRegion message={announce} />
    </section>
  );
}

/** The AI level and kind of document, as two selects with the picked one's description. */
function AiPicker({ id, levels, value, onChange, showExternal }: {
  id: string; levels: ServiceChoice[]; value: FileChoice; onChange: (c: FileChoice) => void; showExternal: boolean;
}) {
  const level = levels.find((l) => l.key === value.ai_tier) ?? levels[0];
  const kinds = level?.kinds ?? [];
  const kind = kinds.find((k) => k.key === (value.ai_kind ?? "general")) ?? kinds[0];
  return (
    <div className="ai-picker">
      {levels.length > 1 && (
        <label>
          <span>AI level</span>
          <select id={`${id}-level`} value={level?.key ?? ""}
            onChange={(e) => onChange({ ai_tier: e.target.value, ai_kind: null })}>
            {levels.map((l) => (
              <option key={l.key} value={l.key}>
                {l.label}{showExternal && l.external ? " (external)" : ""}
                {l.remaining_pages !== null ? ` · ${fmtNum(l.remaining_pages)} pages left` : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      {kinds.length > 1 && (
        <label>
          <span>Kind of document</span>
          <select id={`${id}-kind`} value={kind?.key ?? "general"}
            onChange={(e) => onChange({ ai_tier: level?.key ?? null, ai_kind: e.target.value === "general" ? null : e.target.value })}>
            {kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
        </label>
      )}
      {(kind?.description || level?.description) && (
        <span className="muted small">{kind?.description ?? level?.description}</span>
      )}
    </div>
  );
}

function describe(c: ServiceChoice) {
  const parts = [c.description ?? ""];
  if (c.remaining_pages !== null) parts.push(`${fmtNum(c.remaining_pages)} pages left.`);
  else if (c.kind === "processing") parts.push("No page limit.");
  if (c.max_pages_per_job) parts.push(`At most ${fmtNum(c.max_pages_per_job)} pages per document.`);
  return parts.filter(Boolean).join(" ");
}

function checkCost(level: ServiceChoice | null, chosen: ServiceChoice | undefined) {
  const elsewhere = chosen?.external ? `; nothing is sent to ${chosen.label}` : "";
  if (level?.audit_ai === "charge") return ` and uses ${level.label} pages${elsewhere}`;
  if (level?.audit_ai === "off") return ` and uses no pages; it's made without AI${elsewhere}`;
  return ` and uses no pages${elsewhere}`;
}

/** One notice per distinct reason, listing the files it applies to. */
function groupRefusals(refused: UploadResult[]) {
  const groups = new Map<string, { message: string; files: string[]; overQuota: boolean; supportUrl: string | null }>();
  for (const r of refused) {
    const message = r.error?.message ?? "This file wasn't accepted.";
    const g = groups.get(message) ?? { message, files: [], overQuota: r.error?.error === "over_quota", supportUrl: r.error?.support_url ?? null };
    g.files.push(r.filename);
    groups.set(message, g);
  }
  return Array.from(groups.values());
}
