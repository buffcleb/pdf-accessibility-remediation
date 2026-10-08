import { useEffect } from "react";
import { post } from "../api";

const EVERY = 30000;

/** Tell the portal what's open and in view (a document, a help request, the documents list), every half minute
 * while the page is visible, so it doesn't email news the person is already looking at. Best effort: a failed
 * report only means an email goes as usual. */
export function useSeen(items: (string | null | undefined)[]) {
  const key = items.filter(Boolean).join("|");
  useEffect(() => {
    const list = key ? key.split("|") : [];
    if (list.length === 0) return;
    const report = () => {
      if (document.visibilityState === "visible") void post("/me/seen", { items: list }).catch(() => {});
    };
    report();
    const t = window.setInterval(report, EVERY);
    document.addEventListener("visibilitychange", report);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", report); };
  }, [key]);
}
