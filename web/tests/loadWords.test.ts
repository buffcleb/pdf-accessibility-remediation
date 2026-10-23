// The words of a long wait (#424): sizes known and not, the steps, the large-document note. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ANNOUNCE_EVERY, LARGE_NOTE, READING_TAGS, amount, announceNow, downloading, drawingPage, fraction, isLarge,
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
