import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { decide, isMove, sharedFetch } from "../buildCheck";
import { useHasUnsaved } from "./unsaved";

/* After a deploy, the next move to another page loads it in full, onto the new build (buildCheck.ts). Nothing on a
 * timer: build.json is asked for only when the person moves, and in `vite dev` not at all. */

const check = sharedFetch(
  () => fetch("/build.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
  () => Date.now(),
);

export function NewBuildOnMove() {
  const location = useLocation();
  const hasUnsaved = useHasUnsaved();
  const last = useRef(location);

  useEffect(() => {
    const from = last.current;
    last.current = location;
    if (import.meta.env.DEV || from === location || !isMove(from, location)) return;
    let gone = false;
    void check().then((served) => {
      if (gone) return; // moved on again: that move checks for itself
      let store: Storage | null = null;
      try { store = window.sessionStorage; } catch { /* blocked: never reload */ }
      if (decide(__BUILD_ID__, served, hasUnsaved(), store, Date.now()) === "reload") {
        window.location.assign(location.pathname + location.search + location.hash);
      }
    });
    return () => { gone = true; };
  }, [location, hasUnsaved]);

  return null;
}
