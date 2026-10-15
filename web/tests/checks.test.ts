// Where each version's accessibility check is shown. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkKey, checkLink } from "../src/checks";

test("the engine's results and each saved version have a check tab of their own", () => {
  assert.equal(checkKey({ kind: "original", number: 0, verapdf_file_id: null }), "verapdf_before");
  assert.equal(checkKey({ kind: "primary", number: 1, verapdf_file_id: null }), "verapdf_after");
  assert.equal(checkKey({ kind: "reviewed", number: 3, verapdf_file_id: "f-3" }), "v3");
  assert.equal(checkKey({ kind: "reviewed", number: 4, verapdf_file_id: null }), null);       // none kept: no link
  assert.equal(checkLink("job-1", { kind: "reviewed", number: 3, verapdf_file_id: "f-3" }), "/jobs/job-1?tab=check&check=v3");
});
