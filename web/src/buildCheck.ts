/** After a deploy, a page loaded before it keeps running the old web app. Each build has an id, in the bundle and in
 * /build.json (never cached); when the person moves to another page, the app asks for build.json, and if the id has
 * changed it loads the page in full, which fetches the new build. Never on a timer, never with unsaved changes (it
 * waits for the next move), and never in a loop: the reload guard is staleBuild's (#441). */
import { shouldReload } from "./staleBuild";

/** Clicks this close together share one fetch of build.json. */
export const SHARE_MS = 5_000;

/** The id in build.json's body, or null if it isn't one (a page instead of the file, half a deploy). */
export function servedBuild(body: unknown): string | null {
  const id = (body as { build?: unknown } | null)?.build;
  return typeof id === "string" && id !== "" ? id : null;
}

/** Fetches build.json at most once per SHARE_MS: the id it served, or null when it couldn't say (offline, a 404). */
export function sharedFetch(load: () => Promise<unknown>, now: () => number): () => Promise<string | null> {
  let last: { at: number; id: Promise<string | null> } | null = null;
  return () => {
    const t = now();
    if (!last || t - last.at >= SHARE_MS) {
      last = { at: t, id: load().then(servedBuild, () => null) };
    }
    return last.id;
  };
}

export type Decision = "reload" | "later" | "stay";

/** What to do on a move to another page, given the running build, the one served (null: unknown) and whether the
 * page holds unsaved changes. "later": a new build is out, reload on a move once nothing is unsaved. */
export function decide(running: string, served: string | null, unsaved: boolean,
  store: Pick<Storage, "getItem" | "setItem"> | null, now: number): Decision {
  if (served === null || served === running) return "stay";
  if (unsaved) return "later";
  return shouldReload(store, now) ? "reload" : "stay";
}

/** Whether going from one address to another is a move to another page or tab (the path, or ?tab=), so worth a
 * check; filters and sorting change the query too, and those aren't. */
export function isMove(from: { pathname: string; search: string }, to: { pathname: string; search: string }): boolean {
  if (from.pathname !== to.pathname) return true;
  return new URLSearchParams(from.search).get("tab") !== new URLSearchParams(to.search).get("tab");
}
