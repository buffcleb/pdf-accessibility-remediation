// After a deploy, the next move to another page loads the new build (#575): checked in the built app, on the pages
// scripts/a11y.sh serves. A changed /build.json makes the next click load the page in full; an unchanged one, a
// second change within a minute (the loop guard) and a page with unsaved changes don't.
//
//   node a11y/newBuild.mjs     (run by scripts/a11y.sh after the accessibility checks)
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.A11Y_BASE ?? "http://localhost:4174";
const SEED = JSON.parse(readFileSync(process.env.A11Y_SEED ?? "a11y-seed.json", "utf8"));
const PERSON = "jordan.lee@northfield.example";
const SUPER_ADMIN = "alex.morgan@northfield.example";

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

/** Marks the loaded page, so a full load (which forgets the mark) can be told from a move within the app. */
const mark = (page) => page.evaluate(() => { window.__sameLoad = true; });
const sameLoad = (page) => page.evaluate(() => window.__sameLoad === true);
/** Waits for the move and long enough for any reload it brings to have started and finished. */
async function settle(page, path) {
  await page.waitForURL((url) => url.pathname === path);
  await page.waitForTimeout(1500);
  await page.waitForLoadState("load");
}
/** Has build.json report another build from now on. */
const newBuild = (page, id) => page.route("**/build.json", (route) => route.fulfill({
  contentType: "application/json", headers: { "Cache-Control": "no-store" }, body: JSON.stringify({ build: id }),
}));

const failures = [];
function expect(ok, what) {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures.push(what);
}

const browser = await chromium.launch(process.env.A11Y_CHANNEL ? { channel: process.env.A11Y_CHANNEL } : {});
try {
  const served = await (await fetch(`${BASE}/build.json`)).json();
  expect(typeof served.build === "string" && served.build !== "", `build.json says which build: ${served.build}`);

  let context = await browser.newContext();
  let page = await signIn(context, PERSON);
  const nav = page.getByRole("navigation");

  await mark(page);
  await page.locator(`header a[href="/settings"]`).click();
  await settle(page, "/settings");
  expect(await sameLoad(page), "the same build: the click moves within the app");

  await newBuild(page, "a-newer-build");
  await page.waitForTimeout(5500); // past the fetch the last click shared
  await mark(page);
  await nav.getByRole("link", { name: "My documents" }).click();
  await settle(page, "/");
  expect(!(await sameLoad(page)), "a new build: the next click loads the page in full");

  // The reloaded page still sees another build (as during a deploy with old and new pods): no reload for a minute
  await page.waitForTimeout(5500); // past the shared fetch
  await mark(page);
  await page.locator(`header a[href="/settings"]`).click();
  await settle(page, "/settings");
  expect(await sameLoad(page), "still another build within a minute: no second reload");
  await context.close();

  // Unsaved changes: staying keeps them and the page; once they're gone the next click reloads
  context = await browser.newContext();
  page = await signIn(context, SUPER_ADMIN);
  await page.goto(`${BASE}/admin/groups/${SEED.group}`);
  await page.getByRole("link", { name: "Services and pages" }).click(); // in the app, so Back stays in it too
  await settle(page, `/admin/groups/${SEED.group}/services`);
  await page.getByRole("button", { name: /^Edit Commercial AI$/ }).click();
  await page.locator("#sfg-cap").fill("40");
  await newBuild(page, "a-newer-build");
  await page.waitForTimeout(5500); // past the fetch the click to this page shared
  await mark(page);
  await page.evaluate(() => history.back()); // the dialog covers the page's links
  await page.getByRole("button", { name: "Stay on this page" }).click();
  await page.waitForTimeout(1500);
  expect(await sameLoad(page) && (await page.locator("#sfg-cap").inputValue()) === "40",
    "unsaved changes: no reload, the edit is still there");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Discard changes" }).click();
  await page.waitForTimeout(5500); // past the fetch the last move shared
  await page.locator(`header a[href="/settings"]`).click();
  await settle(page, "/settings");
  expect(!(await sameLoad(page)), "changes discarded: the next click loads the new build");
  await context.close();
} finally {
  await browser.close();
}
if (failures.length) {
  console.error(`${failures.length} failed`);
  process.exit(1);
}
