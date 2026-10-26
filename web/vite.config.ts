import { execSync } from "node:child_process";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// This build's id (#575): WEB_BUILD_ID (scripts/build.sh passes the image tag), else the commit, else the time. It
// goes into the bundle and into /build.json, which a page already open compares when the person moves on.
function buildId(): string {
  if (process.env.WEB_BUILD_ID) return process.env.WEB_BUILD_ID;
  try {
    return execSync("git rev-parse --short=10 HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
  } catch {
    return `t${Date.now().toString(36)}`;
  }
}
const BUILD_ID = buildId();

const buildJson: Plugin = {
  name: "build-json",
  apply: "build",
  generateBundle() {
    this.emitFile({ type: "asset", fileName: "build.json", source: JSON.stringify({ build: BUILD_ID }) + "\n" });
  },
};

// In development the API is proxied, so the app and API share an origin (as with the nginx image).
export default defineConfig({
  plugins: [react(), buildJson],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.PORTAL_API ?? "http://localhost:8080", changeOrigin: false } },
  },
  build: { sourcemap: false, assetsInlineLimit: 0 },
});
