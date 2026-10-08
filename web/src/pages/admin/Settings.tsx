import { useState, type FormEvent, type ReactNode } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import {
  contrastRatio, del, get, patch, post, uploadLogo, type MailStatus, type MailTestResult, type NotifyChoice,
  type RuntimeSettings,
} from "../../api";
import { ErrorNotice, Loading, Notice, useLoad, useTitle } from "../../components/ui";
import { useUnsavedChanges } from "../../components/unsaved";

/** Service-wide settings, one topic per tab; each tab saves only its own fields. */
export function SettingsArea() {
  useTitle("Settings");
  return (
    <>
      <h1>Settings</h1>
      <nav className="tabs" aria-label="Settings sections">
        <NavLink to="/admin/settings" end>Branding</NavLink>
        <NavLink to="/admin/settings/help">Help and messages</NavLink>
        <NavLink to="/admin/settings/email">Email</NavLink>
        <NavLink to="/admin/settings/limits">Uploads and limits</NavLink>
        <NavLink to="/admin/settings/sign-in">Sign-in and data</NavLink>
      </nav>
      <Routes>
        <Route index element={<BrandingSettings />} />
        <Route path="help" element={<HelpSettings />} />
        <Route path="email" element={<MailSettings />} />
        <Route path="limits" element={<LimitSettings />} />
        <Route path="sign-in" element={<SignInSettings />} />
        <Route path="*" element={<Navigate to="/admin/settings" replace />} />
      </Routes>
    </>
  );
}

// --- shared pieces -------------------------------------------------------------------------------

/** Loads the settings and keeps a draft of this tab's changes; Save sends only the changed fields. */
function useSettingsDraft() {
  const settings = useLoad(() => get<RuntimeSettings>("/admin/settings"), []);
  const [draft, setDraft] = useState<Partial<RuntimeSettings>>({});
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState("");
  const dirty = Object.keys(draft).length > 0;
  useUnsavedChanges(dirty);
  const set = (p: Partial<RuntimeSettings>) => { setDraft((d) => ({ ...d, ...p })); setSaved(""); };

  async function save(e: FormEvent, message = "Saved.") {
    e.preventDefault();
    setError(null);
    try {
      settings.setData(await patch<RuntimeSettings>("/admin/settings", draft));
      setDraft({});
      setSaved(message);
    } catch (err) {
      setError(err);
    }
  }
  const s = settings.data ? { ...settings.data, ...draft } : null;
  return { settings, s, set, save, dirty, error, setError, saved, setSaved };
}

function SaveBar({ dirty, saved, label = "Save" }: { dirty: boolean; saved: string; label?: string }) {
  return (
    <div className="actions save-bar">
      <button className="btn" type="submit" disabled={!dirty}>{label}</button>
      {dirty && <span className="small unsaved-note">Unsaved changes</span>}
      {!dirty && saved && <span role="status" className="muted">{saved}</span>}
    </div>
  );
}

function Section({ title, intro, children }: { title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <section className="settings-section">
      <h3>{title}</h3>
      {intro && <p className="muted small">{intro}</p>}
      {children}
    </section>
  );
}

function NumberField({ id, label, hint, unit, value, onChange, min = 1 }: {
  id: string; label: string; hint?: string; unit: string; value: number; onChange: (n: number) => void; min?: number;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="input-unit">
        <input id={id} type="number" min={min} step={1} value={String(value)} aria-describedby={hint ? `${id}-hint` : undefined}
          onChange={(e) => onChange(Number(e.target.value))} />
        <span aria-hidden="true">{unit}</span>
      </div>
      {hint && <span id={`${id}-hint`} className="hint">{hint}</span>}
    </div>
  );
}

function TextField({ id, label, hint, type = "text", value, placeholder, onChange }: {
  id: string; label: string; hint?: string; type?: string; value: string; placeholder?: string; onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type={type} value={value} placeholder={placeholder} aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(e) => onChange(e.target.value)} />
      {hint && <span id={`${id}-hint`} className="hint">{hint}</span>}
    </div>
  );
}

function MessageField({ id, label, hint, value, onChange }: {
  id: string; label: string; hint: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <span id={`${id}-hint`} className="hint">{hint}</span>
      <textarea id={id} value={value} aria-describedby={`${id}-hint`} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

// --- tabs ----------------------------------------------------------------------------------------

function BrandingSettings() {
  const { settings, s, set, save, dirty, error, setError, saved, setSaved } = useSettingsDraft();
  if (settings.loading && !settings.data) return <Loading />;
  if (!s) return <ErrorNotice error={settings.error} />;
  const accent = s.accent_color ?? "";
  const accentOk = /^#[0-9a-fA-F]{6}$/.test(accent);
  const ratio = accentOk ? contrastRatio(accent, "#ffffff") : null;
  const logoUrl = s.logo_content_type ? `/api/v1/branding/logo?v=${s.logo_version}` : null;

  async function logo(file: File | undefined) {
    if (!file) return;
    setError(null);
    try {
      await uploadLogo(file);
      settings.setData(await get<RuntimeSettings>("/admin/settings"));
      setSaved("Logo uploaded.");
    } catch (err) {
      setError(err);
    }
  }

  async function removeLogo() {
    setError(null);
    try {
      await del("/admin/branding/logo");
      settings.setData(await get<RuntimeSettings>("/admin/settings"));
      setSaved("Logo removed.");
    } catch (err) {
      setError(err);
    }
  }

  return (
    <form className="panel" onSubmit={(e) => void save(e, "Saved. Reload the page to see the new branding everywhere.")} aria-label="Branding">
      <Section title="Names" intro="Shown in the header, page titles, the sign-in page, the footer and emails.">
        <div className="grid">
          <TextField id="b-service" label="Service name" value={s.service_name} onChange={(v) => set({ service_name: v })} />
          <TextField id="b-org" label="Organization name" value={s.organization_name} onChange={(v) => set({ organization_name: v })} />
          <TextField id="b-from" label="Email sender name" hint="Optional. Without it, emails use the sender address's own name." value={s.mail_from_name ?? ""}
            onChange={(v) => set({ mail_from_name: v || null })} />
        </div>
      </Section>
      <Section title="Accent color" intro="Used for links and buttons in the light theme. It needs 4.5:1 contrast against white to stay readable.">
        <div className="actions">
          <input id="b-accent-pick" type="color" aria-label="Pick the accent color" value={accentOk ? accent : "#1f4e8c"}
            onChange={(e) => set({ accent_color: e.target.value })} />
          <label htmlFor="b-accent" className="visually-hidden">Accent color as a hex code</label>
          <input id="b-accent" type="text" placeholder="#1f4e8c (default)" value={accent} style={{ maxWidth: 170 }}
            onChange={(e) => set({ accent_color: e.target.value || null })} />
          {ratio !== null && (
            <span className={ratio >= 4.5 ? "pill good" : "pill bad"}>
              {ratio.toFixed(1)}:1 against white{ratio >= 4.5 ? "" : ": too light, needs 4.5:1"}
            </span>
          )}
          {s.accent_color && <button type="button" className="linkbtn small" onClick={() => set({ accent_color: null })}>Use the default</button>}
        </div>
        {accentOk && (
          <div className="accent-preview" aria-hidden="true">
            <span className="btn small" style={{ background: accent, borderColor: accent, color: "#fff" }}>Sample button</span>
            <span style={{ color: accent, textDecoration: "underline" }}>Sample link</span>
          </div>
        )}
      </Section>
      <Section title="Logo" intro="Shown in the header and on the sign-in page. Uploading or removing it takes effect straight away.">
        <div className="logo-row">
          <div className="logo-preview">
            {logoUrl ? <img src={logoUrl} alt={s.logo_alt || "Current logo"} />
              : <span className="muted small">No logo yet: the service's own icon is shown</span>}
          </div>
          <div className="field">
            <label htmlFor="b-logo">Upload a logo <span className="hint">(PNG, JPEG or WebP, up to 512 KB)</span></label>
            <input id="b-logo" type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => void logo(e.target.files?.[0])} />
          </div>
          {logoUrl && <button type="button" className="btn secondary small" onClick={() => void removeLogo()}>Remove logo</button>}
        </div>
        <TextField id="b-alt" label="Logo alt text" hint="What a screen reader says; usually the organization's name." value={s.logo_alt}
          onChange={(v) => set({ logo_alt: v })} />
        {logoUrl && !s.logo_alt && <Notice kind="warn">Add alt text for the logo, so screen-reader users know what it is.</Notice>}
      </Section>
      <ErrorNotice error={error} />
      <SaveBar dirty={dirty} saved={saved} label="Save branding" />
    </form>
  );
}

function HelpSettings() {
  const { settings, s, set, save, dirty, error, saved } = useSettingsDraft();
  if (settings.loading && !settings.data) return <Loading />;
  if (!s) return <ErrorNotice error={settings.error} />;
  return (
    <form className="panel" onSubmit={(e) => void save(e)} aria-label="Help and messages">
      <Section title="Where people get help">
        <div className="grid">
          <TextField id="h-email" type="email" label="Help email address" hint="Shown in the footer and on the sign-in page." value={s.contact_email ?? ""}
            onChange={(v) => set({ contact_email: v || null })} />
          <TextField id="h-url" type="url" label="Link for requesting pages" hint="A ticket form; shown when someone runs out of pages." placeholder="https://"
            value={s.support_url ?? ""} onChange={(v) => set({ support_url: v || null })} />
        </div>
      </Section>
      <Section title="Messages people see">
        <MessageField id="h-notprov" label="Email address not in any group" hint="Shown at sign-in when nobody's domain matches."
          value={s.not_provisioned_message} onChange={(v) => set({ not_provisioned_message: v })} />
        <MessageField id="h-disabled" label="Account disabled" hint="Shown at sign-in to someone whose account is disabled."
          value={s.account_disabled_message} onChange={(v) => set({ account_disabled_message: v })} />
        <MessageField id="h-quota" label="Out of pages" hint="Shown when a document can't be sent; a group can set its own in Group settings."
          value={s.over_quota_message} onChange={(v) => set({ over_quota_message: v })} />
      </Section>
      <ErrorNotice error={error} />
      <SaveBar dirty={dirty} saved={saved} />
    </form>
  );
}

function LimitSettings() {
  const { settings, s, set, save, dirty, error, saved } = useSettingsDraft();
  const models = useLoad(() => get<{ models: string[] }>("/admin/ai-models").catch(() => ({ models: [] })), []);
  if (settings.loading && !settings.data) return <Loading />;
  if (!s) return <ErrorNotice error={settings.error} />;
  return (
    <form className="panel" onSubmit={(e) => void save(e)} aria-label="Uploads and limits">
      <Section title="Each upload" intro="Services can set a lower page limit of their own.">
        <div className="grid">
          <NumberField id="l-mb" label="Largest file" unit="MB" value={s.max_file_mb} onChange={(n) => set({ max_file_mb: n })} />
          <NumberField id="l-pages" label="Most pages per document" unit="pages" value={s.max_pages_per_document}
            onChange={(n) => set({ max_pages_per_document: n })} />
          <NumberField id="l-files" label="Most files per upload" unit="files" value={s.max_files_per_upload}
            onChange={(n) => set({ max_files_per_upload: n })} />
        </div>
      </Section>
      <Section title="Help requests" intro="Files people and support attach, and how long someone can reopen a finished request.">
        <div className="grid">
          <NumberField id="l-hfile" label="Largest attached file" unit="MB" value={s.help_max_file_mb}
            onChange={(n) => set({ help_max_file_mb: n })} />
          <NumberField id="l-hreq" label="All files on one request" unit="MB" value={s.help_max_request_mb}
            onChange={(n) => set({ help_max_request_mb: n })} />
          <NumberField id="l-hreopen" label="Reopen within" unit="days" hint="After it's resolved or closed."
            value={s.help_reopen_days} onChange={(n) => set({ help_reopen_days: n })} />
        </div>
      </Section>
      <Section title="Each person's queue" intro="So one big batch doesn't hold everyone else up.">
        <div className="grid">
          <NumberField id="l-conc" label="Processed at once" unit="documents" hint="The rest wait their turn."
            value={s.max_concurrent_jobs_per_user} onChange={(n) => set({ max_concurrent_jobs_per_user: n })} />
          <NumberField id="l-queue" label="Waiting at most" unit="documents" hint="Uploads beyond this are refused."
            value={s.max_queued_jobs_per_user} onChange={(n) => set({ max_queued_jobs_per_user: n })} />
        </div>
      </Section>
      <Section title="Large documents" intro={"So a few very long documents don't jam the queue during the day. Large ones are " +
        "mixed in between smaller ones, and go one after another when nothing smaller is waiting. Super admins can " +
        "use Process now in Queues when one is urgent."}>
        <div className="grid">
          <NumberField id="l-large" label="Large means more than" unit="pages" min={0} hint="0 turns this off."
            value={s.large_doc_pages} onChange={(n) => set({ large_doc_pages: n })} />
          <NumberField id="l-every" label="Start one large document after every" unit="smaller ones"
            value={s.large_doc_every} onChange={(n) => set({ large_doc_every: n })} />
          <NumberField id="l-once" label="Large documents processed at once" unit="documents"
            value={s.large_doc_at_once} onChange={(n) => set({ large_doc_at_once: n })} />
        </div>
      </Section>
      <Section title="Kind of document: Let AI decide" intro={"Offers \"Let AI decide\" among an AI level's kinds of " +
        "document. The first pages' text (never images) is read to pick the kind before the document is sent; General " +
        "is used when it isn't sure."}>
        <label className="check">
          <input type="checkbox" checked={s.classifier_jev} onChange={(e) => set({ classifier_jev: e.target.checked })} />
          <span>Use Jev through OpenRouter <span className="muted small">(an outside service, about $0.0001 per
            document; never for local-only groups; needs the engine's OpenRouter key)</span></span>
        </label>
        <div className="grid">
          <div className="field">
            <label htmlFor="l-cmodel">Local model</label>
            <select id="l-cmodel" value={s.classifier_local_model} aria-describedby="l-cmodel-hint"
              onChange={(e) => set({ classifier_local_model: e.target.value })}>
              <option value="">None</option>
              {[...new Set([...(models.data?.models ?? []), s.classifier_local_model].filter(Boolean))].map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <span id="l-cmodel-hint" className="hint">A gateway model on your own servers, for local-only groups (and for
              everyone when Jev is off).</span>
          </div>
          <NumberField id="l-cthresh" label="Use the kind when at least" unit="% sure" min={0}
            hint="Below this, General is used." value={Math.round(s.classifier_threshold * 100)}
            onChange={(n) => set({ classifier_threshold: Math.max(0, Math.min(100, n)) / 100 })} />
          <NumberField id="l-cpages" label="Pages read" unit="pages" hint="The first ones, and more spread through long documents."
            value={s.classifier_pages} onChange={(n) => set({ classifier_pages: n })} />
        </div>
      </Section>
      <ErrorNotice error={error} />
      <SaveBar dirty={dirty} saved={saved} />
    </form>
  );
}

function SignInSettings() {
  const { settings, s, set, save, dirty, error, saved } = useSettingsDraft();
  if (settings.loading && !settings.data) return <Loading />;
  if (!s) return <ErrorNotice error={settings.error} />;
  return (
    <form className="panel" onSubmit={(e) => void save(e)} aria-label="Sign-in and data">
      <Section title="Sign-in">
        <div className="grid">
          <NumberField id="s-idle" label="Sign out after no activity for" unit="hours" value={s.session_idle_hours}
            onChange={(n) => set({ session_idle_hours: n })} />
          <NumberField id="s-max" label="Ask to sign in again after" unit="days" hint="Even if they've been active."
            value={s.session_max_days} onChange={(n) => set({ session_max_days: n })} />
        </div>
      </Section>
      <Section title="New groups">
        <label className="check">
          <input type="checkbox" checked={s.new_groups_require_approval} onChange={(e) => set({ new_groups_require_approval: e.target.checked })} />
          <span>New members need approval <span className="muted small">(the starting setting for groups created from now on; each
            group can change it in Group settings)</span></span>
        </label>
      </Section>
      <Section title="How long things are kept" intro="Deleted automatically after this. Shorter keeps less personal data; longer keeps more history for reports.">
        <div className="grid">
          <NumberField id="s-files" label="Files" unit="days" hint="Uploaded and remediated documents."
            value={s.file_retention_days} onChange={(n) => set({ file_retention_days: n })} />
          <NumberField id="s-records" label="Records" unit="days" hint="Usage, page ledger, sign-in and audit records."
            value={s.record_retention_days} onChange={(n) => set({ record_retention_days: n })} />
        </div>
      </Section>
      <ErrorNotice error={error} />
      <SaveBar dirty={dirty} saved={saved} />
    </form>
  );
}

function MailSettings() {
  const status = useLoad(() => get<MailStatus>("/admin/mail"), []);
  const [to, setTo] = useState("");
  const [result, setResult] = useState<MailTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function test(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await post<MailTestResult>("/admin/mail/test", to ? { to } : {}));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <form className="panel" onSubmit={test} aria-label="Email">
      <Section title="How email is sent" intro="The provider and its keys are set in the server's environment (see the operations guide); the sender name is on the Branding tab.">
        {status.loading && !status.data ? <Loading /> : status.data ? (
          <dl className="facts">
            <dt>Provider</dt><dd>{status.data.backend}</dd>
            <dt>From</dt><dd className="mono">{status.data.from_name ? `${status.data.from_name} <${status.data.from_address}>` : status.data.from_address}</dd>
            {status.data.reply_to && <><dt>Replies to</dt><dd className="mono">{status.data.reply_to}</dd></>}
          </dl>
        ) : <ErrorNotice error={status.error} />}
      </Section>
      <Section title="Send a test email">
        <div className="form-row">
          <div className="field" style={{ maxWidth: 360 }}>
            <label htmlFor="m-to">Send to <span className="hint">(empty: yourself)</span></label>
            <input id="m-to" type="email" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <button className="btn" type="submit" disabled={busy}>{busy ? "Sending…" : "Send test email"}</button>
        </div>
        {result && (result.sent
          ? <Notice kind="good" title="Sent">A test email went to {result.to} with {result.backend}. Check it arrived (and not in spam).</Notice>
          : <Notice kind="bad" title="Not sent">{result.error}</Notice>)}
        <ErrorNotice error={error} />
      </Section>
    </form>
    <ReportSchedule />
    </>
  );
}

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hourLabel = (h: number) => new Date(2000, 0, 1, h).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/** When scheduled email reports go out, in which time zone, or not at all. */
function ReportSchedule() {
  const { settings, s, set, save, dirty, error, saved } = useSettingsDraft();
  if (settings.loading && !settings.data) return <Loading />;
  if (!s) return <ErrorNotice error={settings.error} />;
  const zones: string[] = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  return (
    <form className="panel" onSubmit={(e) => void save(e)} aria-label="Notifications and scheduled email reports">
      <Section title="Notification emails" intro={"For people who haven't chosen in their own Settings. Once a day puts " +
        "them in one email at the time below. Sign-in codes and news about someone's own account always go at once."}>
        <div className="grid">
          {([["notify_default_documents", "Documents (done, couldn't be processed, canceled)"],
            ["notify_default_help", "Help requests (replies, changes, reminders)"],
            ["notify_default_admin", "Admin notices (people waiting for approval)"]] as const).map(([key, label]) => (
            <div className="field" key={key}>
              <label htmlFor={`n-${key}`}>{label}</label>
              <select id={`n-${key}`} value={s[key]} onChange={(e) => set({ [key]: e.target.value as NotifyChoice })}>
                <option value="each">As they happen</option>
                <option value="daily">Once a day</option>
                <option value="off">Off</option>
              </select>
            </div>
          ))}
        </div>
      </Section>
      <Section title="Scheduled email reports" intro={"A summary of the last month (or week, or day) in people's inboxes: their documents for " +
        "everyone, a group summary for group admins, a service summary for super admins. Monthly unless people change " +
        "it in their own Settings; every email has a link to stop it. Reports go out on the first working day of the " +
        "month, on Mondays (weekly) or every day (daily)."}>
        <div className="choices">
          <label className="check"><input type="checkbox" checked={s.email_reports_enabled}
            onChange={(e) => set({ email_reports_enabled: e.target.checked })} /> Send scheduled email reports</label>
        </div>
        <div className="grid">
          <div className="field">
            <label htmlFor="r-zone">Time zone</label>
            <input id="r-zone" type="text" list="r-zones" value={s.report_time_zone} aria-describedby="r-zone-hint"
              onChange={(e) => set({ report_time_zone: e.target.value })} />
            <datalist id="r-zones">{zones.map((z) => <option key={z} value={z} />)}</datalist>
            <span id="r-zone-hint" className="hint">Decides when reports go out and what "last month" means, e.g. America/Denver.</span>
          </div>
          <div className="field">
            <label htmlFor="r-hour">Send at <span className="hint">(reports and daily summaries)</span></label>
            <select id="r-hour" value={s.report_send_hour} onChange={(e) => set({ report_send_hour: Number(e.target.value) })}>
              {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
            </select>
          </div>
        </div>
      </Section>
      <ErrorNotice error={error} />
      <SaveBar dirty={dirty} saved={saved} />
    </form>
  );
}
