// What the upload form says while an upload is checked (#560). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { SLOW_UPLOAD_SECONDS, uploadWait } from "../src/components/uploadWait";

test("one file: what's being checked, and not to send it again", () => {
  const m = uploadWait(1, false);
  assert.ok(/^Still checking the file: its pages are counted before it's accepted/.test(m), m);
  assert.ok(/look in Your documents before sending it again/.test(m), m);
  assert.ok(!/malware/.test(m), m);
});

test("several files, with malware scanning on", () => {
  const m = uploadWait(3, true);
  assert.ok(/^Still checking the 3 files: their pages are counted, and they're scanned for malware, before they're accepted/.test(m), m);
  assert.ok(/before sending them again/.test(m), m);
});

test("it waits a few seconds before saying so", () => {
  assert.ok(SLOW_UPLOAD_SECONDS >= 3 && SLOW_UPLOAD_SECONDS <= 10);
});
