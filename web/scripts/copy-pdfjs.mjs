// Copy pdf.js runtime data (standard fonts, CMaps, WebAssembly decoders, color profiles) into
// public/pdfjs so the app serves them from its own origin (the CSP allows nothing else).
import { cpSync, mkdirSync, rmSync } from "node:fs";

const src = "node_modules/pdfjs-dist";
const dst = "public/pdfjs";
rmSync(dst, { recursive: true, force: true });
mkdirSync(dst, { recursive: true });
for (const dir of ["cmaps", "standard_fonts", "wasm", "iccs"]) {
  cpSync(`${src}/${dir}`, `${dst}/${dir}`, { recursive: true });
}
console.log("copied pdf.js data to", dst);
