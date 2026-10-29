import { useState, type FormEvent, type ReactNode } from "react";
import { del, get, post, type ServiceChoice, type UploadOptions } from "../api";
import { DialogButton } from "./AdminDialog";
import { withNoAi } from "./noAi";
import { ErrorNotice, Loading, fmtDate, useLoad } from "./ui";
import { useUnsavedChanges } from "./unsaved";

/* Personal API tokens for scripts (#518): made, listed and revoked here; a token itself can't reach this page's API. */

type Token = {
  id: string; name: string; prefix: string; can_submit: boolean; can_status: boolean; download: "all" | "files" | null;
  download_kinds: string[] | null; processing: string; ai_tier: string | null; ai_kind: string | null; sends_to: string;
  auto_confirm: boolean; created_at: string; expires_at: string; last_used_at: string | null; revoked_at: string | null;
  state: "active" | "expired" | "revoked";
};
type Tokens = { allowed: boolean; why_not: string | null; default_days: number; max_days: number; per_person: number;
  download_kinds: string[]; tokens: Token[] };

const FILES: Record<string, string> = {
  primary: "Remediated PDF (the improved copy for Adobe documents)", enhanced: "Improved copy of Adobe's file",
  visual_fallback: "Visually exact PDF", report_pdf: "Report (PDF)", input: "The original",
  music_files: "Music: every line (zip)", music_score: "Music: the whole piece", math_list: "Math: formula list",
  math_files: "Math: MathML files (zip)", math_nemeth: "Math in braille: Nemeth", math_ueb: "Math in braille: UEB",
};

function can(t: Token) {
  const parts = ["Submit"];
  if (t.can_status) parts.push("check status");
  if (t.download === "all") parts.push("download everything");
  if (t.download === "files") parts.push(`download ${(t.download_kinds ?? []).map((k) => FILES[k]?.split(" (")[0] ?? k).join(", ")}`);
  return parts.join(", ");
}

/** The person's API tokens: what each sends to and may do, when it expires and was last used; revoke; make a new one.
 * `wrap` puts it in its settings card; nothing is shown to someone who can't make tokens and has none. */
export function ApiTokens({ wrap }: { wrap: (children: ReactNode) => ReactNode }) {
  const list = useLoad(() => get<Tokens>("/me/api-tokens"), []);
  const [error, setError] = useState<unknown>(null);
  const data = list.data;
  if (list.loading && !data) return null;
  if (!data) return wrap(<ErrorNotice error={list.error} />);
  if (!data.allowed && data.tokens.length === 0) return null;
  const live = data.tokens.filter((t) => t.state === "active").length;

  async function revoke(t: Token) {
    if (!window.confirm(`Revoke "${t.name}"? Scripts using it stop working at once.`)) return;
    setError(null);
    try {
      await del(`/me/api-tokens/${t.id}`);
      await list.reload();
    } catch (e) {
      setError(e);
    }
  }

  return wrap(
    <>
      {!data.allowed && <p className="muted small">{data.why_not}</p>}
      <ErrorNotice error={error} />
      {data.tokens.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Token</th><th scope="col">Sends to</th><th scope="col">May</th>
              <th scope="col">Expires</th><th scope="col">Last used</th><th scope="col"><span className="visually-hidden">Revoke</span></th></tr></thead>
            <tbody>
              {data.tokens.map((t) => (
                <tr key={t.id}>
                  <td><strong>{t.name}</strong> <span className="mono small muted">{t.prefix}…</span>
                    {t.state !== "active" && <> <span className="pill muted">{t.state === "revoked" ? "Revoked" : "Expired"}</span></>}</td>
                  <td className="small">{t.sends_to}{t.auto_confirm ? " · confirms automatically" : ""}</td>
                  <td className="small">{can(t)}</td>
                  <td className="small">{fmtDate(t.expires_at, false)}</td>
                  <td className="small">{t.last_used_at ? fmtDate(t.last_used_at, false) : <span className="muted">never</span>}</td>
                  <td>{t.state === "active" && <button type="button" className="btn secondary small danger-text" onClick={() => void revoke(t)}>
                    Revoke<span className="visually-hidden"> {t.name}</span></button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.allowed && (
        <p>{live >= data.per_person
          ? <span className="muted small">You have {data.per_person} tokens, the most you can have. Revoke one to make another.</span>
          : <DialogButton label="Make a token" title="Make an API token" formId="new-token" wide>
            {(close) => <NewToken tokens={data} onDone={() => { void list.reload(); }} onClose={close} />}
          </DialogButton>}</p>
      )}
    </>
  );
}

/** The form for a new token, then the token itself, shown once. */
function NewToken({ tokens, onDone, onClose }: { tokens: Tokens; onDone: () => void; onClose: () => void }) {
  const choices = useLoad(() => get<ServiceChoice[]>("/services"), []);
  const processing = (choices.data ?? []).filter((c) => c.kind === "processing");
  const options = useLoad(() => get<UploadOptions>("/upload-options"), []);
  // The AI levels, and No AI where the group offers it (#659)
  const levels = withNoAi((choices.data ?? []).filter((c) => c.kind === "ai_tier"), options.data?.no_ai);
  const [name, setName] = useState("");
  const [service, setService] = useState("");
  const [level, setLevel] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState(false);
  const [download, setDownload] = useState<"" | "all" | "files">("");
  const [kinds, setKinds] = useState<string[]>(["primary"]);
  const [confirm, setConfirm] = useState(false);
  const [days, setDays] = useState(String(tokens.default_days));
  const [made, setMade] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useUnsavedChanges(made === null && !!(name.trim() || service || level || status || download));
  const svc = service || processing.find((c) => c.is_default)?.key || processing[0]?.key || "standard";
  const lvl = levels.find((c) => c.key === (level || levels.find((x) => x.is_default)?.key));
  const asksFirst = [processing.find((c) => c.key === svc), lvl].some((c) => c?.confirm_before_send);

  async function make(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await post<{ token: string }>("/me/api-tokens", {
        name: name.trim(), processing: svc, ai_tier: lvl?.key ?? null, ai_kind: kind || null, can_status: status,
        download: download || null, download_kinds: download === "files" ? kinds : null, auto_confirm: asksFirst && confirm,
        days: Number(days) || null,
      });
      setMade(r.token);
      onDone();
    } catch (err) {
      setError(err);
    }
  }

  if (made) {
    return (
      <div className="dialog-form">
        <p><strong>Copy your token now.</strong> It isn't shown again: if you lose it, revoke it and make another.</p>
        <label htmlFor="tok-secret" className="visually-hidden">Your new API token</label>
        <input id="tok-secret" className="mono" type="text" readOnly value={made} onFocus={(e) => e.target.select()} />
        <div className="actions">
          <button type="button" className="btn" onClick={() => { void navigator.clipboard.writeText(made).then(() => setCopied(true)); }}>
            {copied ? "Copied" : "Copy"}</button>
          <button type="button" className="btn secondary" onClick={onClose}>Done</button>
        </div>
        <p className="small muted" role="status">{copied ? "Copied to the clipboard." : ""}</p>
        <p className="small muted">Send it as <span className="mono">Authorization: Bearer &lt;token&gt;</span>. Documents it sends
          appear in My documents.</p>
      </div>
    );
  }
  if (choices.loading && !choices.data) return <Loading />;
  return (
    <form id="new-token" className="dialog-form" onSubmit={make}>
      <div className="field"><label htmlFor="tok-name">Name <span className="hint">(what it's for, e.g. "lab scanner")</span></label>
        <input id="tok-name" type="text" maxLength={100} required value={name} onChange={(e) => setName(e.target.value)} /></div>
      <div className="form-row">
        <div className="field"><label htmlFor="tok-svc">Processing</label>
          <select id="tok-svc" value={svc} onChange={(e) => setService(e.target.value)}>
            {processing.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select></div>
        {levels.length > 0 && <div className="field"><label htmlFor="tok-level">AI level</label>
          <select id="tok-level" value={lvl?.key ?? ""} onChange={(e) => { setLevel(e.target.value); setKind(""); }}>
            {levels.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select></div>}
        {lvl && lvl.kinds.length > 1 && <div className="field"><label htmlFor="tok-kind">Kind of document</label>
          <select id="tok-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {lvl.kinds.map((k) => <option key={k.key} value={k.key === "general" ? "" : k.key}>{k.label}</option>)}
          </select></div>}
      </div>
      <fieldset className="choices">
        <legend>It may</legend>
        <label className="check"><input type="checkbox" checked disabled /><span>Submit documents (always)</span></label>
        <label className="check"><input type="checkbox" checked={status || download !== ""} disabled={download !== ""}
          onChange={(e) => setStatus(e.target.checked)} /><span>Check their status</span></label>
      </fieldset>
      <fieldset className="choices">
        <legend>Download results</legend>
        <label className="check"><input type="radio" name="tok-dl" checked={download === ""} onChange={() => setDownload("")} />
          <span>No: people download them from My documents</span></label>
        <label className="check"><input type="radio" name="tok-dl" checked={download === "all"} onChange={() => setDownload("all")} />
          <span>Everything: each document's zip</span></label>
        <label className="check"><input type="radio" name="tok-dl" checked={download === "files"} onChange={() => setDownload("files")} />
          <span>Chosen files</span></label>
        {download === "files" && <div className="tok-files">
          {tokens.download_kinds.map((k) => (
            <label className="check" key={k}><input type="checkbox" checked={kinds.includes(k)}
              onChange={(e) => setKinds(e.target.checked ? [...kinds, k] : kinds.filter((x) => x !== k))} /><span>{FILES[k] ?? k}</span></label>
          ))}
        </div>}
      </fieldset>
      {asksFirst && <label className="check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
        <span>Confirm sending automatically <span className="muted">(this option asks before documents go to an outside
          service; otherwise confirm each batch in My documents)</span></span></label>}
      <div className="field" style={{ maxWidth: 240 }}><label htmlFor="tok-days">Lasts <span className="hint">(days, at most {tokens.max_days})</span></label>
        <input id="tok-days" type="number" min={1} max={tokens.max_days} value={days} onChange={(e) => setDays(e.target.value)} /></div>
      <div className="actions"><button className="btn" type="submit" disabled={!name.trim() || (download === "files" && kinds.length === 0)}>
        Make token</button></div>
      <ErrorNotice error={error} />
    </form>
  );
}

/** A group's people's tokens, for its admins (#518): whose, what each sends to and may do, when it expires and was
 * last used, and Revoke. Never the tokens themselves. */
export function GroupApiTokens({ groupId }: { groupId: string }) {
  const list = useLoad(() => get<(Token & { owner_email: string | null; revoked_reason: string | null })[]>(`/groups/${groupId}/api-tokens`), [groupId]);
  const [error, setError] = useState<unknown>(null);
  if (list.loading && !list.data) return <Loading />;
  if (!list.data) return <ErrorNotice error={list.error} />;
  if (list.data.length === 0) return <p className="muted small">Nobody in the group has made a token yet.</p>;

  async function revoke(t: Token & { owner_email: string | null }) {
    if (!window.confirm(`Revoke ${t.owner_email}'s token "${t.name}"? Scripts using it stop working at once.`)) return;
    setError(null);
    try {
      await del(`/groups/${groupId}/api-tokens/${t.id}`);
      await list.reload();
    } catch (e) {
      setError(e);
    }
  }
  const why = (r: string | null) => r === "group_admin" ? "Revoked by an admin" : r === "unused" ? "Stopped: unused" : "Revoked";
  return (
    <>
      <ErrorNotice error={error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th scope="col">Token · person</th><th scope="col">Sends to</th><th scope="col">May</th>
            <th scope="col">Expires · last used</th><th scope="col"><span className="visually-hidden">Revoke</span></th></tr></thead>
          <tbody>
            {list.data.map((t) => (
              <tr key={t.id}>
                <td><strong>{t.name}</strong> <span className="mono small muted">{t.prefix}…</span>
                  {t.state !== "active" && <> <span className="pill muted">{t.state === "expired" ? "Expired" : why(t.revoked_reason)}</span></>}
                  <div className="small muted">{t.owner_email}</div></td>
                <td className="small">{t.sends_to}</td>
                <td className="small">{can(t)}</td>
                <td className="small">{fmtDate(t.expires_at, false)}
                  <div className="muted">{t.last_used_at ? `used ${fmtDate(t.last_used_at, false)}` : "never used"}</div></td>
                <td>{t.state === "active" && <button type="button" className="btn secondary small danger-text" onClick={() => void revoke(t)}>
                  Revoke<span className="visually-hidden"> {t.owner_email}'s token {t.name}</span></button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
