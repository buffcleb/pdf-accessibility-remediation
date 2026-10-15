// The choices in an AI level's model drop-downs. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { modelChoices } from "../src/pages/admin/models";

test("the gateway's models are offered, each once", () => {
  assert.deepEqual(modelChoices("large-vision", ["local-vision", "large-vision", "local-vision"]).map((c) => c.value),
    ["local-vision", "large-vision"]);
});

test("a model the gateway no longer serves stays selected, and says so", () => {
  const choices = modelChoices("commercial", ["local-vision", "commercial-vision"]);
  assert.deepEqual(choices[0], { value: "commercial", label: "commercial (not served by the gateway)", served: false });
  assert.deepEqual(choices.slice(1).map((c) => c.value), ["local-vision", "commercial-vision"]);
});

test("while the list hasn't loaded, the model set now is still there", () => {
  assert.deepEqual(modelChoices("large-vision", []).map((c) => c.value), ["large-vision"]);
  assert.deepEqual(modelChoices(null, []), []);
});

test("a review model isn't offered its own writer, unless that is what is set", () => {
  assert.deepEqual(modelChoices(null, ["a", "b"], "a").map((c) => c.value), ["b"]);
  assert.deepEqual(modelChoices("a", ["a", "b"], "a").map((c) => c.value), ["a", "b"]);
});
