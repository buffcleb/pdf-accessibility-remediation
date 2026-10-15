// The tag viewer's "Report a problem" link. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { reportLabel, reportLink } from "../src/viewer/report";

const tagOf = (link: string) => JSON.parse(new URL(link, "http://x").searchParams.get("tag")!);

test("with a tag selected it names the version, the tag and its page", () => {
  const link = reportLink("job1", 3, { i: 42, role: "Figure", page: 5, alt: "A chart of sales", text: null }, 2);
  const q = new URL(link, "http://x").searchParams;
  assert.equal(new URL(link, "http://x").pathname, "/jobs/job1");
  assert.equal(q.get("help"), "1");
  assert.equal(q.get("version"), "3");
  assert.equal(q.get("page"), "5");   // the tag's page, not the one in view
  assert.deepEqual(tagOf(link), { i: 42, role: "Figure", text: "A chart of sales" });
  assert.equal(reportLabel({ i: 42, role: "Figure" }), "Report a problem here");
});

test("with nothing selected it is still there, about the version and the page in view", () => {
  const link = reportLink("job1", 3, null, 2);
  const q = new URL(link, "http://x").searchParams;
  assert.equal(q.get("version"), "3");
  assert.equal(q.get("page"), "2");
  assert.equal(q.get("tag"), null);
  assert.equal(reportLink("job1", 3, null, null), "/jobs/job1?help=1&version=3");
  assert.equal(reportLabel(null), "Report a problem");
});

test("a long description is shortened, and odd characters survive the address", () => {
  const link = reportLink("j", 1, { i: 1, role: "P", text: "é & ü ".repeat(60) }, null);
  assert.equal(tagOf(link).text.length, 120);
  assert.ok(tagOf(link).text.startsWith("é & ü"));
});
