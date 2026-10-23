import { useEffect, useRef, useState } from "react";
import { ANNOUNCE_EVERY, announceNow } from "./loadWords";

/** A long wait (#424): a bar when the share done is known, a spinner when not (a still ring with reduced motion),
 *  what is happening in words, and an optional note. Screen readers hear the words at most every few seconds. */
export function LoadProgress({ lines, fraction = null, note = null, className = "", quiet = false }: {
  lines: string[]; fraction?: number | null; note?: string | null; className?: string;
  quiet?: boolean; // shown, not announced: another LoadProgress on the page already says the same
}) {
  const text = lines.join(". ");
  const [said, setSaid] = useState(text);
  const last = useRef<{ text: string; at: number } | null>(null);
  useEffect(() => {
    const now = Date.now();
    if (announceNow(last.current, text, now)) {
      last.current = { text, at: now };
      setSaid(text);
      return;
    }
    if (last.current?.text === text) return;
    // Held back: said when the time is up, unless a newer message replaces it first
    const t = window.setTimeout(() => {
      last.current = { text, at: Date.now() };
      setSaid(text);
    }, Math.max(0, ANNOUNCE_EVERY - (now - last.current!.at)));
    return () => window.clearTimeout(t);
  }, [text]);

  return (
    <div className={`load-progress ${className}`.trim()}>
      {fraction === null ? <span className="load-spinner" aria-hidden="true" /> : (
        <span className="load-bar" aria-hidden="true">
          <span className="load-bar-fill" style={{ width: `${Math.round(fraction * 100)}%` }} />
        </span>
      )}
      {/* The words as they change; screen readers get them, held back to every few seconds, from the status */}
      <div className="load-lines" aria-hidden={quiet ? undefined : true}>
        {lines.map((l, i) => <p key={i}>{l}</p>)}
        {note && <p className="load-note small">{note}</p>}
      </div>
      {!quiet && (
        <div className="visually-hidden" role="status" aria-live="polite">
          <span>{said}</span>
          {note && <span>. {note}</span>}
        </div>
      )}
    </div>
  );
}
