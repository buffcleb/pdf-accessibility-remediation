// Accessibility checks of the web app's main pages (#369): axe-core in a headless browser, against WCAG 2.2 A and AA,
// in light and dark. Run by scripts/a11y.sh, which starts the app with invented people and documents
// (tests/a11y/serve.py) and passes what it made in A11Y_SEED.
//
//   node a11y/axe.mjs                  every page
//   A11Y_ONLY=tags node a11y/axe.mjs   just the pages whose name contains "tags"
//
// Exits 1 when axe finds anything, listing each rule with the elements it found; a11y/report.json has the details.
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const AXE = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const BASE = process.env.A11Y_BASE ?? "http://localhost:4174";
const SEED = JSON.parse(readFileSync(process.env.A11Y_SEED ?? "a11y-seed.json", "utf8"));
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const PERSON = "jordan.lee@northfield.example";
const GROUP_ADMIN = "priya.shah@northfield.example";
const SUPER_ADMIN = "alex.morgan@northfield.example";
const PAGES = [
  { name: "sign in", as: null, path: "/login" },
  { name: "my documents", as: PERSON, path: "/" },
  { name: "document: review", as: PERSON, path: `/jobs/${SEED.done}` },
  { name: "document: report", as: PERSON, path: `/jobs/${SEED.done}?tab=report` },
  { name: "document: in progress", as: PERSON, path: `/jobs/${SEED.processing}` },
  { name: "tags: tag manager", as: PERSON, path: `/jobs/${SEED.done}/tags`, wait: ".pv-top" },
  { name: "my settings", as: PERSON, path: "/settings" },
  { name: "group: overview", as: GROUP_ADMIN, path: `/admin/groups/${SEED.group}` },
  { name: "group: people", as: GROUP_ADMIN, path: `/admin/groups/${SEED.group}/people` },
  { name: "group: documents", as: GROUP_ADMIN, path: `/admin/groups/${SEED.group}/documents` },
  { name: "admin: attention", as: SUPER_ADMIN, path: "/admin" },
  { name: "admin: groups", as: SUPER_ADMIN, path: "/admin/groups" },
  { name: "admin: people", as: SUPER_ADMIN, path: "/admin/people" },
  { name: "admin: services", as: SUPER_ADMIN, path: "/admin/services" },
  { name: "admin: help", as: SUPER_ADMIN, path: "/admin/help" },
  { name: "admin: queues", as: SUPER_ADMIN, path: "/admin/queues" },
  { name: "admin: system", as: SUPER_ADMIN, path: "/admin/system" },
  { name: "admin: reports", as: SUPER_ADMIN, path: "/admin/reports" },
  { name: "admin: settings", as: SUPER_ADMIN, path: "/admin/settings" },
].filter((p) => !process.env.A11Y_ONLY || p.name.includes(process.env.A11Y_ONLY));

async function signIn(context, email) {
  const page = await context.newPage();
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: /email me a code/i }).click();
  const code = page.locator('input[autocomplete="one-time-code"], input[inputmode="numeric"]');
  await code.waitFor();
  const { code: value } = await (await page.request.get(`${BASE}/api/v1/__last_code?email=${encodeURIComponent(email)}`)).json();
  await code.fill(value);
  await code.press("Enter");
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  return page;
}

async function check(page, target, scheme) {
  await page.emulateMedia({ colorScheme: scheme });
  await page.goto(`${BASE}${target.path}`, { waitUntil: "load" });
  // The app keeps polling for live updates, so the network never goes quiet: wait for its placeholders to go
  await page.waitForFunction(() => !/Loading…/.test(document.querySelector("main")?.innerText ?? "Loading…"),
    null, { timeout: 15000 }).catch(() => {});
  if (target.wait) await page.locator(target.wait).first().waitFor();
  await page.waitForTimeout(500);  // the last renders after the data arrives
  await page.addScriptTag({ content: AXE });
  const result = await page.evaluate((tags) => window.axe.run(document, { runOnly: { type: "tag", values: tags } }), TAGS);
  return result.violations.map((v) => ({
    page: target.name, scheme, rule: v.id, impact: v.impact, help: v.help, url: v.helpUrl,
    nodes: v.nodes.map((n) => ({ target: n.target.join(" "), summary: n.failureSummary })),
  }));
}

const browser = await chromium.launch(process.env.A11Y_CHANNEL ? { channel: process.env.A11Y_CHANNEL } : {});
const found = [];
try {
  for (const who of [...new Set(PAGES.map((p) => p.as))]) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = who ? await signIn(context, who) : await context.newPage();
    for (const target of PAGES.filter((p) => p.as === who)) {
      for (const scheme of ["light", "dark"]) {
        const v = await check(page, target, scheme);
        found.push(...v);
        console.log(`${v.length ? "✗" : "✓"} ${target.name} (${scheme})${v.length ? `: ${v.map((x) => x.rule).join(", ")}` : ""}`);
      }
    }
    await context.close();
  }
} finally {
  await browser.close();
}
writeFileSync(new URL("./report.json", import.meta.url), JSON.stringify(found, null, 2));
if (found.length) {
  console.log(`\n${found.length} problem(s):`);
  for (const v of found) {
    console.log(`\n${v.page} (${v.scheme}) — ${v.rule} [${v.impact}]: ${v.help}\n  ${v.url}`);
    for (const n of v.nodes.slice(0, 5)) console.log(`  • ${n.target}\n    ${n.summary.replace(/\n/g, "\n    ")}`);
    if (v.nodes.length > 5) console.log(`  … and ${v.nodes.length - 5} more`);
  }
  process.exit(1);
}
console.log(`\nNo problems found on ${PAGES.length} pages, in light and dark.`);
