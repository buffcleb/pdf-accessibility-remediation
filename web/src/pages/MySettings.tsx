import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  del, get, patch, post, put, signOut, type EmailReportChoice, type EmailReports, type NotificationChoice,
  type Notifications as NotificationsData, type NotifyChoice, type ReportView,
} from "../api";
import { useAuth } from "../auth";
import { isAdmin } from "../permissions";
import { VIEW_FREQUENCIES } from "../components/ScheduleView";
import { ErrorNotice, Loading, Notice, useLoad, useTitle } from "../components/ui";
import { useConfirmLeave } from "../components/unsaved";
import { ApiTokens } from "../components/ApiTokens";

const NOTIFY: [NotifyChoice, string][] = [["each", "As they happen"], ["daily", "Once a day"], ["off", "Off"]];
const REPORT_FREQUENCIES: [EmailReportChoice["frequency"], string][] = [
  ["monthly", "Monthly"], ["weekly", "Weekly"], ["daily", "Daily"], ["off", "Off"],
];

/** Each person's own settings: which emails they get and how often. Choices save as they're made. */
export function MySettings() {
  useTitle("Settings");
  const { me } = useAuth();
  const admin = isAdmin(me);
  return (
    <div className="settings-page">
      <header className="settings-intro">
        <h1>Settings</h1>
        <p className="muted">Signed in as <strong>{me?.email}</strong>. Changes are saved as you make them.</p>
        <nav className="settings-nav" aria-label="On this page">
          <a href="#notifications">Email notifications</a>
          <a href="#reports">Email reports</a>
          {admin && <a href="#views">Scheduled report views</a>}
          <a href="#devices">Signed-in devices</a>
        </nav>
      </header>
      <Notifications />
      <EmailReportsCard />
      <ReportViews isAdmin={admin} />
      <ApiTokens wrap={(children) => (
        <Card id="tokens" title="API tokens"
          intro="For scripts that send documents, and if you choose, check on them and fetch the results. Each token sends to one processing option and AI level. Documents it sends appear in My documents.">
          {children}
        </Card>
      )} />
      <Devices />
    </div>
  );
}

/** Sign out everywhere (#354): every browser and device this account is signed in on, this one included. For a lost
 * laptop, or a shared computer left signed in. */
function Devices() {
  const navigate = useNavigate();
  const confirmLeave = useConfirmLeave();
  const [busy, setBusy] = useState(false);
  async function everywhere() {
    if (!(await confirmLeave())) return;
    setBusy(true);
    await signOut(true);
    navigate("/login", { replace: true, state: null });
  }
  return (
    <Card id="devices" title="Signed-in devices"
      intro="Signed in on a computer you no longer have, or one other people use? Sign out everywhere: every browser and device, this one too. You'll need a new code to sign in again.">
      <div className="setting-row">
        <button className="btn secondary" disabled={busy} onClick={() => void everywhere()}>Sign out of all devices</button>
      </div>
    </Card>
  );
}

// --- shared pieces -------------------------------------------------------------------------------------

function Card({ id, title, intro, foot, children, action }: {
  id: string; title: string; intro: ReactNode; foot?: ReactNode; children: ReactNode; action?: ReactNode;
}) {
  return (
    <section className="settings-card" id={id} aria-labelledby={`${id}-title`}>
      <div className="settings-card-head">
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          <p className="muted">{intro}</p>
        </div>
        {action}
      </div>
      {children}
      {foot && <div className="settings-card-foot">{foot}</div>}
    </section>
  );
}

/** A labeled set of mutually exclusive choices, shown as joined buttons. */
function Segmented<T extends string>({ name, label, options, value, onChange }: {
  name: string; label: string; options: [T, string][]; value: T; onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map(([v, text]) => (
        <label key={v} className={value === v ? "on" : undefined}>
          <input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} />{text}
        </label>
      ))}
    </div>
  );
}

/** "Saving…", then "Saved" for a moment, next to the control that changed. */
function useSaveState() {
  const [state, setState] = useState<Record<string, "saving" | "saved">>({});
  const timers = useRef<Record<string, number>>({});
  useEffect(() => () => Object.values(timers.current).forEach((t) => window.clearTimeout(t)), []);
  function mark(key: string, s: "saving" | "saved" | null) {
    window.clearTimeout(timers.current[key]);
    setState((x) => {
      const next = { ...x };
      if (s) next[key] = s; else delete next[key];
      return next;
    });
    if (s === "saved") timers.current[key] = window.setTimeout(() => mark(key, null), 2500);
  }
  return { state, mark };
}

function SaveTick({ state }: { state?: "saving" | "saved" }) {
  return <span className={`save-tick ${state ?? ""}`} aria-hidden="true">{state === "saving" ? "Saving…" : state === "saved" ? "✓ Saved" : ""}</span>;
}

// --- email notifications -------------------------------------------------------------------------------

function Notifications() {
  const list = useLoad(() => get<NotificationsData>("/me/notifications"), []);
  const { state, mark } = useSaveState();
  const [error, setError] = useState<unknown>(null);
  const [announce, setAnnounce] = useState("");

  async function save(key: string, body: object, optimistic: NotificationsData, said: string) {
    const before = list.data;
    list.setData(optimistic);
    setError(null);
    mark(key, "saving");
    try {
      list.setData(await put<NotificationsData>("/me/notifications", body));
      mark(key, "saved");
      setAnnounce(`Saved: ${said}.`);
    } catch (err) {
      list.setData(before);
      mark(key, null);
      setError(err);
    }
  }

  const data = list.data;
  const choose = (c: NotificationChoice, v: NotifyChoice) => data && void save(c.category, { choices: { [c.category]: v } },
    { ...data, choices: data.choices.map((x) => (x.category === c.category ? { ...x, choice: v } : x)) },
    `${c.label}, ${NOTIFY.find(([k]) => k === v)?.[1]}`);
  const setSkipSeen = (c: NotificationChoice, skip: boolean) => data && void save(`${c.category}-seen`,
    { even_if_seen: { [c.category]: !skip } },
    { ...data, choices: data.choices.map((x) => (x.category === c.category ? { ...x, even_if_seen: !skip } : x)) },
    `${c.label}, ${skip ? "skip what I already have open" : "email me even if I have it open"}`);

  return (
    <Card id="notifications" title="Email notifications"
      intro={<>How often each kind of email comes. <strong>Once a day</strong> puts them together in one email.</>}
      foot="Sign-in codes and news about your own account always come straight away. Everything is in the portal whatever you choose.">
      {list.loading && !data ? <div className="setting-row"><Loading /></div> : !data ? <ErrorNotice error={list.error} /> : (
        <ul className="setting-rows">
          {data.choices.map((c) => (
            <li className="setting-row" key={c.category}>
              <div className="setting-text">
                <h3>{c.label}</h3>
                <p>{c.description}</p>
                {c.category === "help" && c.choice === "off" && <p className="setting-warn">You won't be emailed replies or
                  reminders, and a request waiting for your reply closes after 10 days.</p>}
                {c.even_if_seen !== null && c.choice !== "off" && (
                  <label className="check setting-sub">
                    <input type="checkbox" checked={!c.even_if_seen} onChange={(e) => setSkipSeen(c, e.target.checked)} />
                    <span>{c.category === "help" ? "Not while I have the request open (no email for each reply while chatting)"
                      : "Not while I have the document open in the portal"}</span>
                    <SaveTick state={state[`${c.category}-seen`]} />
                  </label>
                )}
              </div>
              <div className="setting-control">
                <Segmented name={`nt-${c.category}`} label={`How often to email ${c.label}`} options={NOTIFY}
                  value={c.choice} onChange={(v) => choose(c, v)} />
                <SaveTick state={state[c.category]} />
              </div>
            </li>
          ))}
        </ul>
      )}
      <ErrorNotice error={error} />
      <div role="status" className="visually-hidden">{announce}</div>
    </Card>
  );
}

// --- email reports -------------------------------------------------------------------------------------

function EmailReportsCard() {
  const reports = useLoad(() => get<EmailReports>("/me/email-reports"), []);
  const { state, mark } = useSaveState();
  const [error, setError] = useState<unknown>(null);
  const [announce, setAnnounce] = useState("");
  const [sample, setSample] = useState("");
  const data = reports.data;

  async function choose(r: EmailReportChoice, f: EmailReportChoice["frequency"]) {
    if (!data) return;
    const before = data;
    reports.setData({ ...data, reports: data.reports.map((x) => (x.kind === r.kind ? { ...x, frequency: f } : x)) });
    setError(null);
    mark(r.kind, "saving");
    try {
      reports.setData(await put<EmailReports>("/me/email-reports", { reports: { [r.kind]: f } }));
      mark(r.kind, "saved");
      setAnnounce(`Saved: ${r.label}, ${f}.`);
    } catch (err) {
      reports.setData(before);
      mark(r.kind, null);
      setError(err);
    }
  }

  async function sendSample() {
    setError(null);
    setSample("");
    try {
      const r = await post<{ subject: string; to: string }>("/me/email-reports/sample");
      setSample(`A sample is on its way to ${r.to}.`);
    } catch (err) {
      setError(err);
    }
  }

  return (
    <Card id="reports" title="Email reports"
      intro="A summary in your inbox, so you don't have to sign in to keep track."
      action={data && data.reports.length > 0 ? <button type="button" className="btn secondary small" onClick={() => void sendSample()}>
        Email me a sample</button> : undefined}
      foot="Monthly reports cover the month before and arrive on its first working day; weekly ones arrive on Mondays; daily ones every day. Each email has a link to stop it.">
      {data && !data.enabled && <div className="setting-row"><Notice kind="info" title="Email reports are switched off for now">
        Your choices are kept for when they're switched back on.</Notice></div>}
      {reports.loading && !data ? <div className="setting-row"><Loading /></div> : !data ? <ErrorNotice error={reports.error} /> :
        data.reports.length === 0 ? <p className="setting-row muted">No email reports apply to your account.</p> : (
          <ul className="setting-rows">
            {data.reports.map((r) => (
              <li className="setting-row" key={r.kind}>
                <div className="setting-text">
                  <h3>{r.label}</h3>
                  <p>{r.description}</p>
                </div>
                <div className="setting-control">
                  <Segmented name={`rp-${r.kind}`} label={`How often to send ${r.label}`} options={REPORT_FREQUENCIES}
                    value={r.frequency} onChange={(f) => void choose(r, f)} />
                  <SaveTick state={state[r.kind]} />
                </div>
              </li>
            ))}
          </ul>
        )}
      <ErrorNotice error={error} />
      {sample && <p role="status" className="setting-row setting-note">{sample}</p>}
      <div role="status" className="visually-hidden">{announce}</div>
    </Card>
  );
}

// --- scheduled report views ----------------------------------------------------------------------------

/** Views I set up (change how often, who gets it and the CSV, or delete) and views others send me (stop getting
 * them). New ones are made from a report: Email this view. */
function ReportViews({ isAdmin }: { isAdmin: boolean }) {
  const views = useLoad(() => get<ReportView[]>("/me/report-views"), []);
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  if (!isAdmin && !(views.data?.length)) return null;

  async function act(fn: () => Promise<unknown>, done: string) {
    setError(null);
    setMsg("");
    try {
      await fn();
      setMsg(done);
      await views.reload();
      return true;
    } catch (e) {
      setError(e);
      return false;
    }
  }

  return (
    <Card id="views" title="Scheduled report views"
      intro={<>Reports emailed on a schedule, with the filters they were saved with. Make one with <strong>Email this view</strong> on any report.</>}>
      {views.loading && !views.data ? <div className="setting-row"><Loading /></div> : !views.data?.length ? (
        <p className="setting-row muted">None yet. Open <Link to="/admin/reports">Reports</Link>, set it up the way you want,
          and use <strong>Email this view</strong>.</p>
      ) : (
        <ul className="setting-rows">
          {views.data.map((v) => (
            <li className="setting-row view-row" key={v.id}>
              <div className="setting-text">
                <h3><Link to={v.link}>{v.name}</Link></h3>
                <p>{v.report_label} · {VIEW_FREQUENCIES.find(([f]) => f === v.frequency)?.[1] ?? v.frequency}
                  {v.attach_csv ? " · with CSV" : ""}{v.mine ? "" : ` · set up by ${v.owner}`}</p>
                <p className="view-people">To {v.recipients.map((p) => p.email).join(", ") || "nobody"}</p>
              </div>
              <div className="setting-control">
                {v.mine && <button className="btn secondary small" aria-expanded={editing === v.id}
                  onClick={() => setEditing(editing === v.id ? null : v.id)}>Edit<span className="visually-hidden"> {v.name}</span></button>}
                <button className="btn secondary small" onClick={() => void act(() => post(`/me/report-views/${v.id}/sample`),
                  `A sample of "${v.name}" is on its way.`)}>Email me a sample<span className="visually-hidden"> of {v.name}</span></button>
                {v.mine
                  ? <button className="btn secondary small danger-text" onClick={() => {
                    if (window.confirm(`Delete "${v.name}"? Nobody will get it any more.`)) {
                      void act(() => del(`/me/report-views/${v.id}`), `Deleted "${v.name}".`);
                    }
                  }}>Delete<span className="visually-hidden"> {v.name}</span></button>
                  : <button className="btn secondary small" onClick={() => void act(() => post(`/me/report-views/${v.id}/leave`),
                    `You won't get "${v.name}" any more.`)}>Stop getting it<span className="visually-hidden"> ({v.name})</span></button>}
              </div>
              {editing === v.id && <EditView view={v} onCancel={() => setEditing(null)}
                onSave={async (changes) => { if (await act(() => patch(`/me/report-views/${v.id}`, changes), `Saved "${v.name}".`)) setEditing(null); }} />}
            </li>
          ))}
        </ul>
      )}
      <ErrorNotice error={error} />
      {msg && <p role="status" className="setting-row setting-note">{msg}</p>}
    </Card>
  );
}

/** Change how often a view goes, who gets it (anyone with the same access) and the CSV. */
function EditView({ view, onSave, onCancel }: {
  view: ReportView; onSave: (changes: Partial<Pick<ReportView, "frequency" | "attach_csv">> & { recipients: string[] }) => Promise<void>;
  onCancel: () => void;
}) {
  const people = useLoad(() => get<{ id: string; email: string }[]>(
    `/me/report-views/recipients?report=${view.report}${view.group_id ? `&group_id=${view.group_id}` : ""}`), [view.id]);
  const [frequency, setFrequency] = useState(view.frequency);
  const [to, setTo] = useState(new Set(view.recipients.map((p) => p.id)));
  const [csv, setCsv] = useState(view.attach_csv);
  return (
    <div className="view-edit" role="group" aria-label={`Edit ${view.name}`}>
      <div className="view-edit-field">
        <span className="view-edit-label" id={`ve-f-${view.id}`}>How often</span>
        <Segmented name={`ve-f-${view.id}`} label="How often" options={VIEW_FREQUENCIES} value={frequency} onChange={setFrequency} />
      </div>
      <fieldset className="view-edit-field">
        <legend className="view-edit-label">Send to</legend>
        {people.loading && !people.data ? <Loading /> : !people.data ? <ErrorNotice error={people.error} /> : (
          <div className="choices">
            {people.data.map((p) => (
              <label className="check" key={p.id}>
                <input type="checkbox" checked={to.has(p.id)} onChange={(e) => {
                  const next = new Set(to);
                  if (e.target.checked) next.add(p.id); else next.delete(p.id);
                  setTo(next);
                }} /><span>{p.email}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>
      <label className="check"><input type="checkbox" checked={csv} onChange={(e) => setCsv(e.target.checked)} />
        <span>Attach every row as a CSV</span></label>
      <div className="actions">
        <button type="button" className="btn small" disabled={to.size === 0}
          onClick={() => void onSave({ frequency, attach_csv: csv, recipients: [...to] })}>Save</button>
        <button type="button" className="btn secondary small" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
