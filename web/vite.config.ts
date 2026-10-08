import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API is proxied, so the app and API share an origin (as with the nginx image).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.PORTAL_API ?? "http://localhost:8080", changeOrigin: false } },
  },
  build: { sourcemap: false, assetsInlineLimit: 0 },
});
