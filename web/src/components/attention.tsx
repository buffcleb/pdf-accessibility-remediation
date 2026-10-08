import { useState, type FormEvent } from "react";
import { get, type AccessCheck, type AttentionItem } from "../api";
import { ErrorNotice, Loading, Notice, useLoad } from "./ui";

const KIND: Record<AttentionItem["severity"], "bad" | "warn" | "info"> = { bad: "bad", warn: "warn", info: "info" };
const SEVERITY: Record<AttentionItem["severity"], string> = { bad: "Problem", warn: "Warning", info: "Note" };

/** What needs an administrator's attention, most serious first, each with what to do about it. */
export function AttentionList({ path, title = "Needs attention" }: { path: string; title?: string }) {
  const items = useLoad(() => get<AttentionItem[]>(path), [path]);
  return (
    <section className="panel" aria-labelledby="att-h">
      <div className="page-head" style={{ alignItems: "center" }}>
        <h2 id="att-h">{title}</h2>
        <button className="btn secondary small" onClick={() => void items.reload()}>Refresh</button>
      </div>
      {items.loading && !items.data && <Loading />}
      <ErrorNotice error={items.error} />
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
              </div>
            </li>
          ))}
        </ul>
      )}
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
