import { useRef, useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ApiError, requestCode, verifyCode } from "../api";
import { useAuth } from "../auth";
import { BotCheck } from "../components/BotCheck";
import { SiteFooter } from "../components/Layout";
import { ErrorNotice, Notice, useTitle } from "../components/ui";

export function Login() {
  useTitle("Sign in");
  const { me, setMe, config } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  // The bot check, when the service has it on: each code request uses up one token, so it's renewed after each
  const siteKey = config.botCheckSiteKey;
  const [botToken, setBotToken] = useState<string | null>(null);
  const [botReset, setBotReset] = useState(0);
  const waitingForCheck = !!siteKey && !botToken;

  if (me) {
    const to = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={to} replace />;
  }

  async function sendCode(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setBlocked(null);
    try {
      await requestCode(email, botToken);
      setStep("code");
      setCode("");
      window.setTimeout(() => codeRef.current?.focus(), 0);
    } catch (err) {
      // Not provisioned / disabled: show the message the administrators configured
      if (err instanceof ApiError && (err.body.error === "not_provisioned" || err.body.error === "account_disabled")) {
        setBlocked(err.body.message);
      } else {
        setError(err);
      }
    } finally {
      setBusy(false);
      if (siteKey) setBotReset((n) => n + 1);
    }
  }

  async function submitCode(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMe(await verifyCode(email, code.trim()));
    } catch (err) {
      setError(err);
      if (err instanceof ApiError && err.body.error === "account_disabled") setBlocked(err.body.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="login">
      <div className="panel">
        {config.logoUrl && <img src={config.logoUrl} alt={config.logoAlt || config.organization || ""} className="login-logo" />}
        <h1>{config.appName}</h1>
        {config.organization && <p className="muted">{config.organization}</p>}
        {step === "email" ? (
          <form onSubmit={sendCode} className="panel" style={{ border: 0, padding: 0 }} noValidate>
            <p>Sign in with your work email address. We'll email you a 6-digit code.</p>
            <div className="field">
              <label htmlFor="email">Email address</label>
              <input id="email" type="email" autoComplete="email" required value={email} autoFocus
                onChange={(e) => setEmail(e.target.value)} />
            </div>
            {blocked && <Notice kind="warn" title="Can't sign in">{blocked}</Notice>}
            <ErrorNotice error={error} />
            <div className="actions">
              <button className="btn" type="submit" disabled={busy || !email.includes("@") || waitingForCheck}>
                {busy ? "Sending…" : "Email me a code"}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={submitCode} className="panel" style={{ border: 0, padding: 0 }} noValidate>
            <Notice kind="info">
              We sent a code to <strong>{email}</strong>. It expires in 10 minutes.
            </Notice>
            <div className="field">
              <label htmlFor="code">6-digit code</label>
              <input id="code" ref={codeRef} type="text" inputMode="numeric" autoComplete="one-time-code"
                pattern="[0-9]{6}" maxLength={6} required value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
            </div>
            {blocked && <Notice kind="warn" title="Can't sign in">{blocked}</Notice>}
            <ErrorNotice error={error} />
            <div className="actions">
              <button className="btn" type="submit" disabled={busy || code.length !== 6}>
                {busy ? "Checking…" : "Sign in"}
              </button>
              <button className="linkbtn" type="button" onClick={() => setStep("email")}>
                Use a different email
              </button>
              <button className="linkbtn" type="button" disabled={busy || waitingForCheck}
                onClick={(e) => void sendCode(e as unknown as FormEvent)}>
                Send a new code
              </button>
            </div>
          </form>
        )}
        {siteKey && <BotCheck siteKey={siteKey} onToken={setBotToken} resetKey={botReset} />}
      </div>
      <SiteFooter />
    </main>
  );
}
