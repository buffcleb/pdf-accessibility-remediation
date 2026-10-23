/** What the upload form says when an upload takes a while (#560). The portal answers only once every file is
 * checked: its pages counted and, where that's on, scanned for malware. A large document takes long enough that it
 * looks stuck, and a refresh then sends it again. */

/** Seconds of "Uploading…" before the form says why. */
export const SLOW_UPLOAD_SECONDS = 5;

export function uploadWait(files: number, malwareScan: boolean): string {
  const one = files === 1;
  const scanned = malwareScan ? `, and ${one ? "it's" : "they're"} scanned for malware,` : "";
  return `Still checking ${one ? "the file" : `the ${files} files`}: ${one ? "its" : "their"} pages are counted${scanned} ` +
    `before ${one ? "it's" : "they're"} accepted, and a large document takes longer. If you leave or refresh this ` +
    `page, look in Your documents before sending ${one ? "it" : "them"} again: a file already received carries on.`;
}
