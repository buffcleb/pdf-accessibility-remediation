// The words of a long wait (#424): sizes known and not, the steps, the large-document note. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ANNOUNCE_EVERY, LARGE_NOTE, READING_TAGS, amount, announceNow, downloading, drawingPage, fraction, isLarge,
  pageLoading, tagsLoading, type Loading,
} from "../src/components/loadWords";

const MB = 1024 * 1024;

test("a download whose size is known: how much of it has come", () => {
  assert.equal(downloading("the document", { received: 12 * MB, total: 48 * MB }),
    "Downloading the document (12 of 48 MB)");
  assert.equal(amount({ received: 1.5 * MB, total: 4.25 * MB }), "1.5 of 4.3 MB");
  assert.equal(amount({ received: 300 * 1024, total: 900 * 1024 }), "300 of 900 KB");
  assert.equal(amount({ received: 2000, total: 4 * MB }), "0.1 of 4 MB");   // a few bytes in isn't "0"
  assert.equal(amount({ received: 50 * MB, total: 48 * MB }), "48 of 48 MB");  // never more than all of it
});

test("a download whose size isn't known: how much so far", () => {
  assert.equal(downloading("the tags", { received: 12 * MB, total: null }), "Downloading the tags (12 MB so far)");
  assert.equal(amount({ received: 400 * 1024, total: null }), "400 KB so far");
  assert.equal(downloading("the tags", { received: 0, total: null }), "Downloading the tags");
});

test("the steps after the downloads", () => {
  assert.equal(READING_TAGS, "Reading the tags");
  assert.equal(drawingPage(1), "Drawing page 1");
});

test("the share done is known only when every size is", () => {
  assert.equal(fraction([{ received: MB, total: 4 * MB }, { received: MB, total: 4 * MB }]), 0.25);
  assert.equal(fraction([{ received: MB, total: 4 * MB }, { received: MB, total: null }]), null);
  assert.equal(fraction([]), null);
  assert.equal(fraction([{ received: 5 * MB, total: 4 * MB }]), 1);
});

test("a large document gets the note: over 100 pages or 20 MB, where known", () => {
  assert.ok(isLarge({ pages: 101 }));
  assert.ok(isLarge({ bytes: 21 * MB }));
  assert.ok(!isLarge({ pages: 100, bytes: 20 * MB }));
  assert.ok(!isLarge({ pages: null, bytes: null }));
  assert.ok(/minute or two/.test(LARGE_NOTE));
  assert.ok(/Refreshing the page starts it again/.test(LARGE_NOTE));
});

test("screen readers hear a change at most every few seconds", () => {
  assert.ok(announceNow(null, "a", 0));
  assert.ok(!announceNow({ text: "a", at: 0 }, "a", ANNOUNCE_EVERY * 2));      // nothing new
  assert.ok(!announceNow({ text: "a", at: 0 }, "b", ANNOUNCE_EVERY - 1));      // too soon: held back
  assert.ok(announceNow({ text: "a", at: 0 }, "b", ANNOUNCE_EVERY));
});

// #579: the tag map loads on its own, so the tree waits only for it, and the page view for both
const start: Loading = { pdf: { received: 0, total: null }, tags: { received: 0, total: null }, reading: false, pages: null };

test("the tree waits for the tags alone", () => {
  const load = { ...start, pdf: { received: 10 * MB, total: 70 * MB }, tags: { received: 1 * MB, total: 4 * MB } };
  assert.deepEqual(tagsLoading(load), { lines: ["Downloading the tags (1 of 4 MB)"], fraction: 0.25 });
  assert.deepEqual(tagsLoading(null), { lines: ["Downloading the tags"], fraction: null });
  // gzipped: the size isn't known, so a spinner and the bytes so far
  assert.deepEqual(tagsLoading({ ...load, tags: { received: 2 * MB, total: null } }),
    { lines: ["Downloading the tags (2 MB so far)"], fraction: null });
});

test("the page view waits for both, then reads", () => {
  const load = { ...start, pdf: { received: 12 * MB, total: 48 * MB }, tags: { received: 0, total: null } };
  assert.deepEqual(pageLoading(load).lines, ["Downloading the document (12 of 48 MB)", "Downloading the tags"]);
  // the recorded size stands in for a download that doesn't say
  assert.deepEqual(pageLoading({ ...load, pdf: { received: 12 * MB, total: null } }, 48 * MB).lines[0],
    "Downloading the document (12 of 48 MB)");
  // the PDF in and being opened, the tags still coming: only the tags are waited for
  assert.deepEqual(pageLoading({ ...load, reading: true, tags: { received: 1 * MB, total: 2 * MB } }),
    { lines: ["Downloading the tags (1 of 2 MB)"], fraction: 0.5 });
  // both in
  assert.deepEqual(pageLoading({ ...load, reading: true, pages: 332 }), { lines: [READING_TAGS], fraction: null });
  // the tags in first: the document is still downloading
  assert.deepEqual(pageLoading({ ...load, pages: 332, tags: { received: 2 * MB, total: 2 * MB } }).lines,
    ["Downloading the document (12 of 48 MB)", "Downloading the tags (2 of 2 MB)"]);
});
