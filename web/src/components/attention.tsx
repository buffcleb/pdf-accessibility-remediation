import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { del, get, post, type AccessCheck, type AttentionAck, type AttentionItem } from "../api";
import { ErrorNotice, LiveRegion, Loading, Notice, fmtDate, useLoad } from "./ui";

const KIND: Record<AttentionItem["severity"], "bad" | "warn" | "info"> = { bad: "bad", warn: "warn", info: "info" };
const SEVERITY: Record<AttentionItem["severity"], string> = { bad: "Problem", warn: "Warning", info: "Note" };

/** What needs an administrator's attention, most serious first, each with what to do about it, a link to what it's
 * about, and (for a summary or notice) Acknowledge: hidden for everyone who sees this list until something new joins
 * it (#538). */
export function AttentionList({ path, title = "Needs attention" }: { path: string; title?: string }) {
  const items = useLoad(() => get<AttentionItem[]>(path), [path]);
  const [showAcked, setShowAcked] = useState(false);
  const acked = useLoad(() => showAcked ? get<AttentionAck[]>(`${path}/acknowledged`) : Promise.resolve([]), [path, showAcked]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [said, setSaid] = useState("");

  async function act(key: string, title: string, undo: boolean) {
    setBusy(key);
    setError(null);
    try {
      const now = undo ? await del<AttentionItem[]>(`${path}/acknowledge?key=${encodeURIComponent(key)}`)
        : await post<AttentionItem[]>(`${path}/acknowledge`, { key });
      if (now) items.setData(now);
      if (showAcked) void acked.reload();
      setSaid(undo ? `Shown again: ${title}` : `Acknowledged: ${title}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="panel" aria-labelledby="att-h">
      <div className="page-head" style={{ alignItems: "center" }}>
        <h2 id="att-h">{title}</h2>
        <button className="btn secondary small" onClick={() => void items.reload()}>Refresh</button>
      </div>
      {items.loading && !items.data && <Loading />}
      <ErrorNotice error={items.error} />
      <ErrorNotice error={error} />
      <LiveRegion message={said} />
      {items.data && items.data.length === 0 && (
        <Notice kind="good" title="All clear">No pools are empty or running low, and no documents are stuck or failing.</Notice>
      )}
      {items.data && items.data.length > 0 && (
        <ul className="attention-list">
          {items.data.map((i, n) => (
            <li key={`${i.kind}-${i.group_id ?? ""}-${i.service_key ?? ""}-${n}`}>
              {/* Styled like a notice, but not an alert: a list read on arrival shouldn't interrupt */}
              <div className={`notice ${KIND[i.severity]}`}>
                <strong><span className="visually-hidden">{SEVERITY[i.severity]}: </span>{i.title}</strong>
                <span>{i.detail}</span>
                {i.fix && <span className="small"><strong>What to do:</strong> {i.fix}</span>}
                {!!i.earlier && <span className="small muted">Not counting {i.earlier} acknowledged earlier.</span>}
                {(i.link || i.ackable) && (
                  <span className="small attention-actions">
                    {i.link && <Link to={i.link}>{i.link_label ?? "Open"}<span className="visually-hidden">: {i.title}</span></Link>}
                    {i.ackable && (
                      <button type="button" className="btn secondary small" disabled={busy === i.key}
                        onClick={() => void act(i.key, i.title, false)}>
                        {busy === i.key ? "Acknowledging…" : "Acknowledge"}<span className="visually-hidden">: {i.title}</span>
                      </button>
                    )}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      <details className="attention-acked" onToggle={(e) => setShowAcked((e.target as HTMLDetailsElement).open)}>
        <summary>Acknowledged</summary>
        <p className="muted small">Hidden for everyone who sees this list until something new joins them.</p>
        {acked.loading && !acked.data && <Loading />}
        <ErrorNotice error={acked.error} />
        {acked.data && acked.data.length === 0 && <p className="muted small">Nothing acknowledged.</p>}
        {acked.data && acked.data.length > 0 && (
          <ul className="attention-acked-list">
            {acked.data.map((a) => (
              <li key={a.key}>
                <strong>{a.title}</strong>
                <span className="small muted">
                  By {a.acked_by}, {fmtDate(a.acked_at)}. {a.still === 0 ? "All dealt with or past the list's time."
                    : `${a.still} of ${a.count} still there.`}
                </span>
                <button type="button" className="btn secondary small" disabled={busy === a.key}
                  onClick={() => void act(a.key, a.title, true)}>Show again<span className="visually-hidden">: {a.title}</span></button>
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  );
}

/** "Why can't this person use …?": every check between a person and a service, in order. */
export function AccessCheckForm({ path, services }: { path: string; services: { key: string; label: string }[] }) {
  const [email, setEmail] = useState("");
  const [service, setService] = useState(services[0]?.key ?? "");
  const [result, setResult] = useState<AccessCheck | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function check(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await get<AccessCheck>(`${path}?email=${encodeURIComponent(email.trim())}&service=${encodeURIComponent(service)}`));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="ac-h">
      <h2 id="ac-h">Why can't this person use a service?</h2>
      <p className="muted small">Checks everything between a person and a service, in order, and says what to change.</p>
      <form onSubmit={check} className="form-row">
        <div className="field">
          <label htmlFor="ac-email">Person's email</label>
          <input id="ac-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ac-service">Service</label>
          <select id="ac-service" value={service} onChange={(e) => setService(e.target.value)}>
            {services.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </div>
        <div className="actions" style={{ alignSelf: "flex-end" }}>
          <button className="btn" type="submit" disabled={busy || !email.includes("@")}>{busy ? "Checking…" : "Check"}</button>
        </div>
      </form>
      <ErrorNotice error={error} />
      {result && <AccessCheckView result={result} />}
    </section>
  );
}

/** The outcome of an access check: the verdict, then every check with ✓/✕ and the fix for the failing one. */
export function AccessCheckView({ result }: { result: AccessCheck }) {
  return (
    <div role="status">
      <Notice kind={result.can_use ? "good" : "warn"}
        title={result.can_use
          ? `${result.user_email} can use ${result.service_label}`
          : `${result.user_email} can't use ${result.service_label}`}>
        {result.can_use
          ? (result.remaining_pages === null ? "No page limit." : `${result.remaining_pages.toLocaleString()} pages left for them.`)
          : "The first problem below is what stops them."}
      </Notice>
      <ol className="check-steps">
        {result.steps.map((st) => (
          <li key={st.key} className={st.ok ? "ok" : st.blocking ? "fail" : "note"}>
            <span className="step-mark" aria-hidden="true">{st.ok ? "✓" : st.blocking ? "✕" : "!"}</span>
            <span>
              <strong>{st.label}</strong>
              <span className="visually-hidden">{st.ok ? ": passes" : st.blocking ? ": fails" : ": note"}</span>
              {": "}{st.detail}
              {st.fix && <span className="small"><br /><strong>Fix:</strong> {st.fix}</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function serviceOptions(list: { key: string; label: string }[] | null | undefined) {
  return (list ?? []).map((s) => ({ key: s.key, label: s.label }));
}
