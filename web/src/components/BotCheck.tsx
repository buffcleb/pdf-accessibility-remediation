import { useEffect, useRef, useState } from "react";

/** Cloudflare Turnstile, for "Email me a code" when the service turns the bot check on. Most people never see a
 * challenge (it only appears when Cloudflare needs one); what's happening is announced in a live region. */

type Turnstile = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id?: string) => void;
  remove: (id: string) => void;
};
declare global {
  interface Window {
    turnstile?: Turnstile;
  }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let loading: Promise<Turnstile> | null = null;

function load(): Promise<Turnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT;
    s.async = true;
    s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("no turnstile")));
    s.onerror = () => { loading = null; reject(new Error("blocked")); };
    document.head.appendChild(s);
  });
  return loading;
}

type State = "checking" | "done" | "expired" | "failed";

/** Calls onToken with each new token (null when it expires or fails). Change resetKey to get a fresh token after
 * one has been used (each works once). */
export function BotCheck({ siteKey, onToken, resetKey }: { siteKey: string; onToken: (t: string | null) => void; resetKey: number }) {
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const [state, setState] = useState<State>("checking");
  const callback = useRef(onToken);
  callback.current = onToken;

  useEffect(() => {
    let gone = false;
    load().then((t) => {
      if (gone || !box.current || widget.current) return;
      widget.current = t.render(box.current, {
        sitekey: siteKey,
        appearance: "interaction-only", // shown only when Cloudflare needs the person to do something
        size: "flexible",
        callback: (token: string) => { setState("done"); callback.current(token); },
        "expired-callback": () => { setState("expired"); callback.current(null); },
        "error-callback": () => { setState("failed"); callback.current(null); },
      });
    }).catch(() => { if (!gone) { setState("failed"); callback.current(null); } });
    return () => {
      gone = true;
      if (widget.current && window.turnstile) window.turnstile.remove(widget.current);
      widget.current = null;
    };
  }, [siteKey]);

  useEffect(() => {
    if (resetKey && widget.current && window.turnstile) {
      setState("checking");
      callback.current(null);
      window.turnstile.reset(widget.current);
    }
  }, [resetKey]);

  const message = {
    checking: "Checking that this is a person, not a script…",
    done: "Check complete.",
    expired: "The check expired; it's running again.",
    failed: "The check that this is a person couldn't run. Content blockers can stop it: allow challenges.cloudflare.com, or try another browser.",
  }[state];
  return (
    <div className="bot-check">
      <div ref={box} />
      <p className={`small ${state === "failed" ? "" : "muted"}`} role="status" aria-live="polite">{message}</p>
    </div>
  );
}
