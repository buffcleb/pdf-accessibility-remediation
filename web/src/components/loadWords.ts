// The words of a long wait (#424): what is downloading and how far it has got, the step after, and a note on a large
// document. Pure, so the wording is tested on its own (tests/loadWords.test.ts); LoadProgress.tsx shows it.

/** A download under way: bytes received, and the size when the server said it (null when it didn't). */
export type Received = { received: number; total: number | null };

const MB = 1024 * 1024;

/** A document this big (or with more pages) gets the note that it can take a minute or two. */
export const LARGE_BYTES = 20 * MB;
export const LARGE_PAGES = 100;

export const LARGE_NOTE = "A large document can take a minute or two to open. Refreshing the page starts it again "
  + "from the beginning.";

export const READING_TAGS = "Reading the tags";

/** Announced to screen readers at most this often (ms): every chunk would be noise. */
export const ANNOUNCE_EVERY = 4000;

function inMB(n: number): string {
  const mb = n / MB;
  if (mb >= 10) return String(Math.round(mb));
  return mb === 0 ? "0" : String(Math.max(0.1, Math.round(mb * 10) / 10));  // a few bytes in is "0.1", not "0"
}

/** "12 of 48 MB", "300 of 900 KB", or "12 MB so far" when the size isn't known. */
export function amount({ received, total }: Received): string {
  if (total !== null && total > 0) {
    const r = Math.min(received, total);
    if (total < MB) return `${Math.round(r / 1024)} of ${Math.max(1, Math.round(total / 1024))} KB`;
    return `${inMB(r)} of ${inMB(total)} MB`;
  }
  return received < MB ? `${Math.round(received / 1024)} KB so far` : `${inMB(received)} MB so far`;
}

/** "Downloading the document (12 of 48 MB)"; just "Downloading the document" before the first bytes arrive. */
export function downloading(what: string, d: Received): string {
  if (d.received === 0 && (d.total === null || d.total === 0)) return `Downloading ${what}`;
  return `Downloading ${what} (${amount(d)})`;
}

export function drawingPage(page: number): string {
  return `Drawing page ${page}`;
}

/** How far the downloads have got together, 0 to 1, or null when a size isn't known (a spinner, not a bar). */
export function fraction(downloads: Received[]): number | null {
  if (!downloads.length || downloads.some((d) => d.total === null || d.total <= 0)) return null;
  const total = downloads.reduce((n, d) => n + d.total!, 0);
  const got = downloads.reduce((n, d) => n + Math.min(d.received, d.total!), 0);
  return Math.min(1, got / total);
}

/** Large, so worth the note: over LARGE_PAGES pages or a file over LARGE_BYTES, where either is known. */
export function isLarge({ pages, bytes }: { pages?: number | null; bytes?: number | null }): boolean {
  return (pages ?? 0) > LARGE_PAGES || (bytes ?? 0) > LARGE_BYTES;
}

/** Whether a new message is announced now: the first one, or a change once ANNOUNCE_EVERY has passed since the
 *  last. A message held back is announced when the time is up (LoadProgress sets a timer). */
export function announceNow(last: { text: string; at: number } | null, text: string, now: number,
  every = ANNOUNCE_EVERY): boolean {
  if (!last) return true;
  if (last.text === text) return false;
  return now - last.at >= every;
}
