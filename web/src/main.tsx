import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppError } from "./components/AppError";
import { App } from "./App";
import { AuthProvider, type AppConfig } from "./auth";
import { NewBuildOnMove } from "./components/newBuild";
import { UnsavedChangesProvider } from "./components/unsaved";
import "./styles.css";

const DEFAULTS: AppConfig = { apiBase: "", appName: "PDF Accessibility Remediation", organization: "" };

type Branding = {
  service_name: string; organization_name: string; logo_url: string | null; logo_alt: string;
  accent_color: string | null; support_url: string | null; contact_email: string | null;
  bot_check_site_key?: string | null;
};

// The accent color is validated by the server (#rrggbb, 4.5:1 against white). Light theme only: dark mode
// keeps its own accent, chosen for contrast on dark backgrounds. CSS variables are set from script (allowed by
// the page's content security policy, which blocks inline <style>), following the light/dark switch.
function applyAccent(color: string) {
  if (!/^#[0-9a-fA-F]{6}$/.test(color)) return;
  const root = document.documentElement.style;
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  const update = () => {
    if (dark.matches) {
      root.removeProperty("--accent");
      root.removeProperty("--accent-soft");
    } else {
      root.setProperty("--accent", color);
      root.setProperty("--accent-soft", `color-mix(in srgb, ${color} 12%, #ffffff)`);
    }
  };
  update();
  dark.addEventListener("change", update);
}

// Runtime settings come from /config.json so one build deploys to any host; the branding super admins set
// in the app (GET /api/v1/branding) overrides its names.
async function start() {
  let config = DEFAULTS;
  try {
    const r = await fetch("/config.json", { cache: "no-store" });
    if (r.ok) config = { ...DEFAULTS, ...(await r.json()) };
  } catch {
    /* use defaults */
  }
  try {
    const r = await fetch(`${config.apiBase.replace(/\/$/, "")}/api/v1/branding`, { cache: "no-store" });
    if (r.ok) {
      const b = (await r.json()) as Branding;
      config = {
        ...config,
        appName: b.service_name || config.appName,
        organization: b.organization_name || config.organization,
        logoUrl: b.logo_url ? `${config.apiBase.replace(/\/$/, "")}${b.logo_url}` : null,
        logoAlt: b.logo_alt,
        supportUrl: b.support_url,
        contactEmail: b.contact_email,
        botCheckSiteKey: b.bot_check_site_key ?? null,
      };
      if (b.accent_color) applyAccent(b.accent_color);
    }
  } catch {
    /* keep config.json's names */
  }
  document.title = config.appName;
  // A data router, so pages can ask before leaving with unsaved changes; the app's own <Routes> do the routing.
  // After a deploy, the next move to another page loads the new build (newBuild.tsx).
  const router = createBrowserRouter([{ path: "*",
    element: <UnsavedChangesProvider><NewBuildOnMove /><App /></UnsavedChangesProvider>, errorElement: <AppError /> }]);
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <AuthProvider config={config}>
        <RouterProvider router={router} />
      </AuthProvider>
    </StrictMode>,
  );
}

void start();
