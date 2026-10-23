// pdf.js setup and per-version loading (PDF + tag map), cached while the viewer is open.
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { get, getBytes, type TagMap } from "../api";
import type { Received } from "../components/loadWords";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export type PDFDoc = pdfjs.PDFDocumentProxy;
export type PaneData = { pdf: PDFDoc; tagmap: TagMap; destroy: () => void };
// How far a version's loading has got (#424): its two downloads, then reading them (the tags, and the PDF by pdf.js)
export type LoadState = { pdf: Received; tags: Received; reading: boolean; pages: number | null };

const cache = new Map<string, Promise<PaneData>>();
const states = new Map<string, LoadState>();
const watchers = new Map<string, Set<(s: LoadState) => void>>();

function report(key: string, change: Partial<LoadState>) {
  const s = { ...states.get(key)!, ...change };
  states.set(key, s);
  watchers.get(key)?.forEach((fn) => fn(s));
}

/** Follow a version's loading: called now with where it stands, then on each change. Returns the unfollow. */
export function watchVersion(jobId: string, number: number, fn: (s: LoadState) => void): () => void {
  const key = `${jobId}:${number}`;
  if (!watchers.has(key)) watchers.set(key, new Set());
  watchers.get(key)!.add(fn);
  const s = states.get(key);
  if (s) fn(s);
  return () => void watchers.get(key)?.delete(fn);
}

export function loadVersion(jobId: string, number: number): Promise<PaneData> {
  const key = `${jobId}:${number}`;
  let p = cache.get(key);
  if (!p) {
    const none = { received: 0, total: null };
    states.set(key, { pdf: none, tags: none, reading: false, pages: null });
    p = (async () => {
      const [bytes, tagmap] = await Promise.all([
        getBytes(`/jobs/${jobId}/versions/${number}/pdf`,
          (received, total) => report(key, { pdf: { received, total } })),
        get<TagMap>(`/jobs/${jobId}/versions/${number}/tagmap`,
          (received, total) => report(key, { tags: { received, total } })),
      ]);
      report(key, { reading: true, pages: tagmap.pages.length });
      const task = pdfjs.getDocument({
        data: new Uint8Array(bytes),
        cMapUrl: "/pdfjs/cmaps/",
        cMapPacked: true,
        standardFontDataUrl: "/pdfjs/standard_fonts/",
        wasmUrl: "/pdfjs/wasm/",
        iccUrl: "/pdfjs/iccs/",
        enableXfa: false,
      });
      const pdf = await task.promise;
      return { pdf, tagmap, destroy: () => void task.destroy() };
    })();
    p.catch(() => cache.delete(key));
    cache.set(key, p);
  }
  return p;
}

export function clearVersionCache() {
  for (const p of cache.values()) p.then((d) => d.destroy()).catch(() => {});
  cache.clear();
  states.clear();
}
