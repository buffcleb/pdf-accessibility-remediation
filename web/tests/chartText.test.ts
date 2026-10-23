// The report chart's end labels stay inside the chart (#545). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { endName } from "../src/components/chartText";

test("a name that fits is kept; a longer one is shortened with an ellipsis", () => {
  assert.equal(endName("Standard + Commercial AI", 30), "Standard + Commercial AI");
  assert.equal(endName("Adobe Autotag + Commercial AI", 16), "Adobe Autotag +…");
  assert.equal(endName("Adobe Autotag + Commercial AI", 16).length, 16);
  assert.equal(endName("Adobe Autotag", 7), "Adobe…");  // no space before the ellipsis
});
