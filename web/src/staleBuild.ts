/** After a deploy, a page loaded before it still asks for the old build's files, which are gone: the tag manager,
 * loaded only when opened, then fails to load ("Importing a module script failed"). The page reloads once at the same
 * address, which fetches the new build; if that doesn't help, the error page says what happened. */

const STALE = [
  /Importing a module script failed/i, // Safari
  /Failed to fetch dynamically imported module/i, // Chrome, Edge
  /error loading dynamically imported module/i, // Firefox
  /ChunkLoadError|Loading chunk [\w-]+ failed/i, // bundlers' own wording
];

/** A failed load of part of the app, in the words of each browser engine. */
export function isStaleBuildError(err: unknown): boolean {
  const msg = err instanceof Error ? `${err.name} ${err.message}` : String(err ?? "");
  return STALE.some((re) => re.test(msg));
}

const KEY = "reloaded-for-update";
export const RELOAD_GUARD_MS = 60_000;

/** Whether to reload for a stale build now: not if the page already did within the last minute (that reload
 * didn't help, so the error page shows instead of reloading for ever). Notes the reload when it says yes. */
export function shouldReload(store: Pick<Storage, "getItem" | "setItem"> | null, now: number): boolean {
  if (!store) return false; // nowhere to note the reload: never risk a loop
  try {
    const last = Number(store.getItem(KEY) ?? 0);
    if (last && now - last < RELOAD_GUARD_MS) return false;
    store.setItem(KEY, String(now));
    return true;
  } catch {
    return false; // no storage: never risk a loop
  }
}

/** A lazily loaded part of the app that reloads the page once when its file is from a build that's gone. */
export function loadOrReload<T>(load: () => Promise<T>): () => Promise<T> {
  return () => load().catch((err: unknown) => {
    if (isStaleBuildError(err) && shouldReload(window.sessionStorage, Date.now())) {
      window.location.reload();
      return new Promise<T>(() => {}); // the page is going; nothing to show meanwhile
    }
    throw err;
  });
}
