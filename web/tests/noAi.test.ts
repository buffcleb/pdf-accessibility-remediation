// No AI as a choice for remediation (#659). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { NO_AI, noAiChoice, noAiLine, withNoAi } from "../src/components/noAi";
import type { ServiceChoice } from "../src/api";

const level = (key: string, is_default = false): ServiceChoice => ({
  ...noAiChoice(false), key, label: key, is_default, allow_full_review: true, audit_ai: "free",
});

test("not offered: the group's levels as they are", () => {
  const levels = [level("comm", true)];
  assert.equal(withNoAi(levels, "off"), levels);
  assert.equal(withNoAi(levels, undefined), levels);
});

test("offered: No AI after the levels, the group's default level still preselected", () => {
  const out = withNoAi([level("comm", true), level("local")], "on");
  assert.deepEqual(out.map((l) => l.key), ["comm", "local", NO_AI]);
  assert.deepEqual(out.filter((l) => l.is_default).map((l) => l.key), ["comm"]);
});

test("preselected: No AI is the only default", () => {
  const out = withNoAi([level("comm", true)], "default");
  assert.deepEqual(out.filter((l) => l.is_default).map((l) => l.key), [NO_AI]);
});

test("offered with no AI levels: No AI alone", () => {
  assert.deepEqual(withNoAi([], "on").map((l) => l.key), [NO_AI]);
});

test("No AI never reviews figures, and its audits use no AI", () => {
  const c = noAiChoice(false);
  assert.equal(c.allow_full_review, false);
  assert.equal(c.audit_ai, "off");
  assert.equal(c.external, false);
  assert.deepEqual(c.kinds, []);
});

test("the document page's line: the figures left to describe", () => {
  assert.equal(noAiLine(0), "Made without AI.");
  assert.equal(noAiLine(null), "Made without AI.");
  assert.equal(noAiLine(1), "Made without AI: 1 figure has no alt text. Each is listed under To review for you to "
    + "describe in the tag manager.");
  assert.ok(noAiLine(1420).startsWith("Made without AI: 1,420 figures have no alt text."));
});
