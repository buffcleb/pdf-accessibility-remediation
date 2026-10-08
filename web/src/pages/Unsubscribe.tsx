import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { SiteFooter } from "../components/Layout";
import { ErrorNotice, Loading, Notice, useLoad, useTitle } from "../components/ui";

type Info = { report: string; email: string; frequency: string };

/** The link at the bottom of each report email. No sign-in; opening it changes nothing until the button is used
 * (mail scanners open links). */
export function Unsubscribe() {
  useTitle("Stop an email report");
  const { config } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("t") ?? "";
  const info = useLoad(() => get<Info>(`/email-reports/unsubscribe?t=${encodeURIComponent(token)}`), [token]);
  const [done, setDone] = useState<Info | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function stop() {
    setBusy(true);
    setError(null);
    try {
      setDone(await post<Info>("/email-reports/unsubscribe", { token }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const current = done ?? info.data;
  return (
    <>
      <main id="main" className="login">
        <div className="panel">
          <h1>{config.appName}</h1>
          {info.loading && !info.data ? <Loading /> : !current ? <ErrorNotice error={info.error} /> : (
            done || current.frequency === "off" ? (
              <Notice kind="good" title="Done">
                <p>{current.email} won't get &ldquo;{current.report}&rdquo; emails any more. Other emails aren't affected.</p>
                <p>You can turn it back on in <Link to="/settings">Settings</Link> after signing in.</p>
              </Notice>
            ) : (
              <>
                <p>Stop sending &ldquo;{current.report}&rdquo; emails to <strong>{current.email}</strong>?</p>
                <p className="muted small">Only this report stops. You can change it again in Settings after signing in.</p>
                <ErrorNotice error={error} />
                <button className="btn" onClick={() => void stop()} disabled={busy}>{busy ? "Stopping…" : "Stop these emails"}</button>
              </>
            )
          )}
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
