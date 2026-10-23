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
  // Tagged by Adobe with the improved copy as the document people get (#499)
  { name: "document: Adobe, improved copy", as: PERSON, path: `/jobs/${SEED.adobe}` },
  // Opened from My documents (#482): Back, Previous and Next follow the list
  { name: "document: from my documents", as: PERSON, path: `/jobs/${SEED.done}?from=${encodeURIComponent("/?sort=filename")}` },
  { name: "document: in progress", as: PERSON, path: `/jobs/${SEED.processing}` },
  { name: "tags: tag manager", as: PERSON, path: `/jobs/${SEED.done}/tags`, wait: ".pv-top" },
  // Show on a review item no tag holds (#426): its area outlined, the rest of the page dimmed, a label with focus
  { name: "tags: tag manager, an item shown", as: PERSON, path: `/jobs/${SEED.done}/tags?v=1&page=1`,
    wait: ".pv-spot-label" },
  // One page at a time: the page turning laid out like a PDF viewer's (^ | v, the page number box, "of N")
  { name: "tags: tag manager, one page", as: PERSON, path: `/jobs/${SEED.done}/tags`, wait: ".pv-top",
    act: async (page) => {
      await page.locator("#pv-view").selectOption("one");
      await page.locator("#pv-page").waitFor();
    } },
  // While it loads (#424): the PDF's download is held back, so the pane shows its progress
  { name: "tags: tag manager, loading", as: PERSON, path: `/jobs/${SEED.done}/tags`, wait: ".pv-loading",
    hold: "**/versions/*/pdf" },
  // A tag's card open near the top of the pane (#436): with all tags shown, the page's first tag (its title), focused
  // from the keyboard. The card must also be inside the pane, not cut off by its edge
  { name: "tags: tag manager, card near the top", as: PERSON, path: `/jobs/${SEED.done}/tags`, wait: ".pv-top",
    act: async (page) => {
      await page.locator("#pv-mode").selectOption("all");
      await page.locator(".pv-box").first().focus();
      await page.locator(".pv-pop[data-side]").first().waitFor();    },
    inside: ".pv-pop" },
  { name: "my settings", as: PERSON, path: "/settings" },
  { name: "group: overview", as: GROUP_ADMIN, path: `/admin/groups/${SEED.group}` },
  { name: "group: people", as: GROUP_ADMIN, path: `/admin/groups/${SEED.group}/people` },
  { name: "group: services and pages", as: SUPER_ADMIN, path: `/admin/groups/${SEED.group}/services` },
  { name: "group: people, manage dialog", as: SUPER_ADMIN, path: `/admin/groups/${SEED.group}/people`,
    act: async (page) => {
      await page.getByRole("button", { name: /^Manage jordan/ }).click();
      await page.locator("dialog.admin-dialog[open] .access-check, dialog.admin-dialog[open] .person-section").first().waitFor();
    } },
  { name: "admin: groups, create dialog", as: SUPER_ADMIN, path: "/admin/groups",
    act: async (page) => {
      await page.getByRole("button", { name: "Create a group" }).click();
      await page.locator("dialog.admin-dialog[open]").waitFor();
    } },
  // The admin edit dialog (#511): open over the table, then its question after a change and Escape
  { name: "group: services, edit dialog", as: SUPER_ADMIN, path: `/admin/groups/${SEED.group}/services`,
    act: async (page) => {
      await page.getByRole("button", { name: /^Edit Commercial AI$/ }).click();
      await page.locator("dialog.admin-dialog[open]").waitFor();
    } },
  { name: "group: services, unsaved question", as: SUPER_ADMIN, path: `/admin/groups/${SEED.group}/services`,
    act: async (page) => {
      await page.getByRole("button", { name: /^Edit Commercial AI$/ }).click();
      await page.locator("#sfg-cap").fill("40");
      await page.keyboard.press("Escape");
      await page.getByRole("heading", { name: "Save your changes?" }).waitFor();
    } },
  { name: "group: documents", as: GROUP_ADMIN, path: `/admin/groups/${SEED.group}/documents` },
  { name: "admin: attention", as: SUPER_ADMIN, path: "/admin" },
  // An item acknowledged (#538): the list of acknowledged ones, with Show again (the first scheme acknowledges it)
  { name: "admin: attention, acknowledged", as: SUPER_ADMIN, path: "/admin", wait: ".attention-list",
    act: async (page) => {
      const ack = page.getByRole("button", { name: /^Acknowledge/ });
      if (await ack.count()) { await ack.first().click(); await page.waitForTimeout(500); }
      await page.locator(".attention-acked > summary").click();
      await page.locator(".attention-acked-list li").first().waitFor();
    } },
  { name: "admin: groups", as: SUPER_ADMIN, path: "/admin/groups" },
  { name: "admin: people", as: SUPER_ADMIN, path: "/admin/people" },
  { name: "admin: services", as: SUPER_ADMIN, path: "/admin/services" },
  { name: "admin: help", as: SUPER_ADMIN, path: "/admin/help" },
  { name: "admin: queues", as: SUPER_ADMIN, path: "/admin/queues" },
  { name: "admin: system", as: SUPER_ADMIN, path: "/admin/system" },
  // A pod's details with About open: its images' versions and digests (#541); the Versions table is on both
  { name: "admin: system, a pod's about", as: SUPER_ADMIN, path: "/admin/system", wait: ".pod-rows",
    act: async (page) => {
      await page.getByRole("button", { name: /^postgres-0:/ }).click();
      await page.locator(".pod-about > summary").click();
      await page.locator(".pod-about .pod-digest").waitFor();
    } },
  { name: "document: in progress, run details", as: SUPER_ADMIN, path: `/jobs/${SEED.processing}`, wait: ".run-so-far" },
  { name: "admin: system, over time", as: SUPER_ADMIN, path: "/admin/system/over-time" },
  { name: "admin: system, audit export", as: SUPER_ADMIN, path: "/admin/system/audit-export" },
  // Two pages of rows: the pagers above and below the list (#514)
  { name: "admin: reports, audit over two pages", as: SUPER_ADMIN, path: "/admin/reports?report=audit&range=all",
    wait: ".list-pager-top" },
  { name: "admin: reports", as: SUPER_ADMIN, path: "/admin/reports" },
  { name: "admin: settings", as: SUPER_ADMIN, path: "/admin/settings" },
  // Uploads and limits, one sub-tab per topic (#521)
  ...["uploads", "downloads", "ai", "checks", "help", "alerts"].map((t) => (
    { name: `admin: settings, limits: ${t}`, as: SUPER_ADMIN, path: `/admin/settings/limits/${t}` })),
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
  if (target.hold) await page.route(target.hold, () => {});  // never answered while the page is checked
  await page.goto(`${BASE}${target.path}`, { waitUntil: "load" });
  // The app keeps polling for live updates, so the network never goes quiet: wait for its placeholders to go
  await page.waitForFunction(() => !/Loading…/.test(document.querySelector("main")?.innerText ?? "Loading…"),
    null, { timeout: 15000 }).catch(() => {});
  if (target.wait) await page.locator(target.wait).first().waitFor();
  await page.waitForTimeout(500);  // the last renders after the data arrives
  if (target.act) await target.act(page);
  await page.addScriptTag({ content: AXE });
  const result = await page.evaluate((tags) => window.axe.run(document, { runOnly: { type: "tag", values: tags } }), TAGS);
  if (target.hold) await page.unroute(target.hold);
  const found = result.violations.map((v) => ({
    page: target.name, scheme, rule: v.id, impact: v.impact, help: v.help, url: v.helpUrl,
    nodes: v.nodes.map((n) => ({ target: n.target.join(" "), summary: n.failureSummary })),
  }));
  if (target.inside) {
    // Wholly inside the visible part of the scrolling pane it's in
    const cut = await page.locator(target.inside).first().evaluate((el) => {
      const a = el.getBoundingClientRect();
      const b = el.closest(".pv-body").getBoundingClientRect();
      const at = (r) => [r.left, r.top, r.right, r.bottom].map(Math.round).join(", ");
      return a.top < b.top || a.left < b.left || a.bottom > b.bottom || a.right > b.right
        ? `card at ${at(a)}, pane at ${at(b)}` : null;
    });
    if (cut) found.push({ page: target.name, scheme, rule: "card-inside-pane", impact: "serious",
      help: "The tag's card is cut off by the pane's edge (#436)", url: "web/src/viewer/fullText.ts",
      nodes: [{ target: target.inside, summary: cut }] });
  }
  return found;
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
