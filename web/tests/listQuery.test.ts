// A document's page follows the list it was opened from (#482). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { atPage, exportFilters, listSource, safeFrom } from "../src/components/listQuery";

const G = "0b6f1c2e-0000-4000-8000-000000000001";

test("only a path in the app is kept as the way back", () => {
  assert.equal(safeFrom("/?q=x&page=2"), "/?q=x&page=2");
  assert.equal(safeFrom(`/admin/groups/${G}/documents`), `/admin/groups/${G}/documents`);
  for (const bad of ["//evil.example/", "/\\evil.example", "https://evil.example/", "javascript:alert(1)", "", null]) {
    assert.equal(safeFrom(bad), null, String(bad));
  }
});

test("My documents: the list's search, filters and sort, not its page", () => {
  const s = listSource("/?q=report&status=done&sort=filename&page=3&size=50&from=2026-10-01")!;
  assert.equal(s.api, "/documents");
  assert.equal(s.label, "My documents");
  const q = new URLSearchParams(s.query);
  assert.deepEqual([q.get("q"), q.get("status"), q.get("sort"), q.get("page")], ["report", "done", "filename", null]);
  assert.equal(q.get("since"), new Date("2026-10-01T00:00:00").toISOString());  // the date filter, as the list sends it
  assert.equal(new URLSearchParams(listSource("/")!.query).get("sort"), "-created_at");
});

test("a group's documents and the Documents report have neighbours; other lists don't", () => {
  assert.equal(listSource(`/admin/groups/${G}/documents?q=user2%40`)!.api, `/groups/${G}/documents`);
  const all = listSource("/admin/reports?report=documents&status=failed&sort=-pages&page=2")!;
  assert.equal(all.api, "/reports/documents");
  assert.equal(new URLSearchParams(all.query).get("status"), "failed");
  assert.equal(new URLSearchParams(all.query).get("group_id"), null);
  assert.equal(listSource("/admin/reports")!.api, "/reports/documents");  // the report shown first
  assert.equal(new URLSearchParams(listSource(`/admin/groups/${G}/reports?report=documents`)!.query).get("group_id"), G);
  assert.equal(listSource("/admin/reports?report=usage"), null);
  assert.equal(listSource("/admin/queues"), null);
  assert.equal(listSource("/admin/groups/../documents"), null);
});

test("Previous and Next keep the list's address at the page that holds the document, for Back", () => {
  const mine = listSource("/?sort=filename&size=50")!;
  assert.equal(atPage("/?sort=filename&size=50", mine, 50), "/?sort=filename&size=50");
  assert.equal(atPage("/?sort=filename&size=50", mine, 51), "/?sort=filename&size=50&page=2");
  assert.equal(atPage("/?page=2", mine, 25), "/");  // 25 a page unless the list says otherwise
  const report = listSource("/admin/reports?report=documents&page_size=10&page=1")!;
  assert.equal(atPage("/admin/reports?report=documents&page_size=10&page=1", report, 31),
    "/admin/reports?report=documents&page_size=10&page=4");
});

test("exporting everything shown sends the list's search, filters and sort, not its page (#481)", () => {
  const f = exportFilters(new URLSearchParams("q=syllabus&status=done&audit=false&sort=filename&page=3&size=50"));
  assert.deepEqual(f, { q: "syllabus", status: "done", audit: false, sort: "filename" });
  assert.deepEqual(exportFilters(new URLSearchParams("")), { sort: "-created_at" });
});
