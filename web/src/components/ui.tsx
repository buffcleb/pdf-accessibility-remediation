import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, type Job } from "../api";

export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return withTime
    ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function fmtNum(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : n.toLocaleString();
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.body.message;
  return "Something went wrong. Check your connection and try again.";
}

export function Notice({ kind = "info", title, children }: { kind?: "info" | "good" | "warn" | "bad"; title?: string; children?: ReactNode }) {
  return (
    <div className={`notice ${kind}`} role={kind === "bad" ? "alert" : undefined}>
      {title && <strong>{title}</strong>}
      {children}
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  const body = error instanceof ApiError ? error.body : null;
  return (
    <Notice kind="bad">
      <span>{errorMessage(error)}</span>
      {body?.fields && (
        <ul className="small">
          {body.fields.map((f) => (
            <li key={f.field}>
              {f.field}: {f.message}
            </li>
          ))}
        </ul>
      )}
      {body?.support_url && (
        <a href={body.support_url} target="_blank" rel="noreferrer">
          Request more pages
        </a>
      )}
    </Notice>
  );
}

const STATUS: Record<Job["status"], [string, string]> = {
  held: ["Needs your OK", "warn"],
  queued: ["Waiting", "muted"],
  submitted: ["Starting", "info"],
  processing: ["Processing", "info"],
  completed: ["Done", "good"],
  failed: ["Failed", "bad"],
  canceled: ["Canceled", "muted"],
};

export function JobStatus({ job }: { job: Job }) {
  const [label, kind] = STATUS[job.status] ?? [job.status, "muted"];
  return <span className={`pill ${kind}`}>{label}</span>;
}

export function Verdict({ job }: { job: Job }) {
  if (job.status !== "completed" || !job.result) return <span className="muted">–</span>;
  if (job.report_only) return <span className="pill info">Audit only</span>;
  return job.result.compliant_after ? (
    <span className="pill good">Passes PDF/UA-1</span>
  ) : (
    <span className="pill warn">Needs work</span>
  );
}

export function isActive(job: Job) {
  return job.status === "queued" || job.status === "submitted" || job.status === "processing";
}

/** Load data, with a reload function. Poll while `pollWhile` returns true. */
/** Load data, and optionally keep it fresh: while `pollWhile` says so, reload every `pollMs` (paused while the
 * tab is in the background, and caught up as soon as it's visible again). */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[], pollWhile?: (data: T) => boolean, pollMs = 4000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!pollWhile || data === null || !pollWhile(data)) return;
    const t = window.setTimeout(() => {
      if (document.hidden) setTick((n) => n + 1); // try again later, without fetching
      else void reload();
    }, pollMs);
    const onVisible = () => { if (!document.hidden) void reload(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearTimeout(t); document.removeEventListener("visibilitychange", onVisible); };
  }, [data, pollWhile, reload, pollMs, tick]);

  return { data, error, loading, reload, setData };
}

export function Meter({ used, total, label }: { used: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return (
    <div className="meter" role="img" aria-label={`${label}: ${pct}% used`}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Loading() {
  return (
    <p className="muted" role="status">
      Loading…
    </p>
  );
}

/** Announces short status messages to screen readers. */
export function LiveRegion({ message }: { message: string }) {
  return (
    <div className="visually-hidden" role="status" aria-live="polite">
      {message}
    </div>
  );
}

export function useTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · PDF Accessibility Remediation`;
  }, [title]);
}
