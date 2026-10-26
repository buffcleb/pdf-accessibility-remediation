// pdf.js setup and per-version loading (PDF + tag map), cached while the viewer is open.
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { get, getBytes, type TagMap } from "../api";
import type { Loading } from "../components/loadWords";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export type PDFDoc = pdfjs.PDFDocumentProxy;
export type PaneData = { pdf: PDFDoc; tagmap: TagMap; destroy: () => void };
// How far a version's loading has got (#424): its two downloads, then reading them (pdf.js opening the PDF). The
// tag map comes on its own (#579): `pages` is set once it has, whether or not the PDF has.
export type LoadState = Loading;

type Opened = { pdf: PDFDoc; destroy: () => void };
const tagmaps = new Map<string, Promise<TagMap>>();
const pdfs = new Map<string, Promise<Opened>>();
const versions = new Map<string, Promise<PaneData>>();
const states = new Map<string, LoadState>();
const watchers = new Map<string, Set<(s: LoadState) => void>>();

function report(key: string, change: Partial<LoadState>) {
  const s = { ...states.get(key)!, ...change };
  states.set(key, s);
  watchers.get(key)?.forEach((fn) => fn(s));
}

function begin(key: string) {
  if (states.has(key)) return;
  const none = { received: 0, total: null };
  states.set(key, { pdf: none, tags: none, reading: false, pages: null });
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

/** A version's tag map alone: all the tag tree and the reader need, without waiting for the PDF (#579). */
export function loadTagmap(jobId: string, number: number): Promise<TagMap> {
  const key = `${jobId}:${number}`;
  let p = tagmaps.get(key);
  if (!p) {
    begin(key);
    p = get<TagMap>(`/jobs/${jobId}/versions/${number}/tagmap`,
      (received, total) => report(key, { tags: { received, total } }))
      .then((tagmap) => {
        report(key, { pages: tagmap.pages.length });
        return tagmap;
      });
    p.catch(() => tagmaps.delete(key));
    tagmaps.set(key, p);
  }
  return p;
}

function loadPdf(jobId: string, number: number): Promise<Opened> {
  const key = `${jobId}:${number}`;
  let p = pdfs.get(key);
  if (!p) {
    begin(key);
    p = (async () => {
      const bytes = await getBytes(`/jobs/${jobId}/versions/${number}/pdf`,
        (received, total) => report(key, { pdf: { received, total } }));
      report(key, { reading: true });
      const task = pdfjs.getDocument({
        data: new Uint8Array(bytes),
        cMapUrl: "/pdfjs/cmaps/",
        cMapPacked: true,
        standardFontDataUrl: "/pdfjs/standard_fonts/",
        wasmUrl: "/pdfjs/wasm/",
        iccUrl: "/pdfjs/iccs/",
        enableXfa: false,
      });
      try {
        return { pdf: await task.promise, destroy: () => void task.destroy() };
      } catch (e) {
        void task.destroy();
        throw e;
      }
    })();
    p.catch(() => pdfs.delete(key));
    pdfs.set(key, p);
  }
  return p;
}

/** The PDF and its tag map together, for the page view: both downloads start at once. */
export function loadVersion(jobId: string, number: number): Promise<PaneData> {
  const key = `${jobId}:${number}`;
  let p = versions.get(key);
  if (!p) {
    p = Promise.all([loadPdf(jobId, number), loadTagmap(jobId, number)])
      .then(([opened, tagmap]) => ({ ...opened, tagmap }));
    p.catch(() => versions.delete(key));
    versions.set(key, p);
  }
  return p;
}

export function clearVersionCache() {
  for (const p of pdfs.values()) p.then((d) => d.destroy()).catch(() => {});
  pdfs.clear();
  tagmaps.clear();
  versions.clear();
  states.clear();
}
