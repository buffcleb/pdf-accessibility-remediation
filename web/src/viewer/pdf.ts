// pdf.js setup and per-version loading (PDF + tag map), cached while the viewer is open.
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { get, getBytes, type TagMap } from "../api";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export type PDFDoc = pdfjs.PDFDocumentProxy;
export type PaneData = { pdf: PDFDoc; tagmap: TagMap; destroy: () => void };

const cache = new Map<string, Promise<PaneData>>();

export function loadVersion(jobId: string, number: number): Promise<PaneData> {
  const key = `${jobId}:${number}`;
  let p = cache.get(key);
  if (!p) {
    p = (async () => {
      const [bytes, tagmap] = await Promise.all([
        getBytes(`/jobs/${jobId}/versions/${number}/pdf`),
        get<TagMap>(`/jobs/${jobId}/versions/${number}/tagmap`),
      ]);
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
}
