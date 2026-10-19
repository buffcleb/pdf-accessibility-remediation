// A formula's two readings in Edit tag (#403). Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { DISAGREE, choiceFor, chosen, differences, readingsOf } from "../src/viewer/readings";

const finding = {
  issue: DISAGREE,
  extra: {
    formula: "page-003_formula-2.mml", latex: "x^3", mathml: "<math><mi>x</mi></math>",
    differs: { numbers: { ai_only: ["2"], rule_only: ["3"] }, letters: { ai_only: [], rule_only: [] } },
    readings: [{ source: "ai", label: "The AI's reading", text: "x squared" },
               { source: "rule", label: "Read by rule", text: "x cubed" }],
  },
};

test("the two readings come from the element's finding", () => {
  assert.equal(readingsOf([{ issue: "read-aloud text written by AI" }]), null);
  assert.equal(readingsOf([finding])?.formula, "page-003_formula-2.mml");
  assert.equal(readingsOf([{ issue: DISAGREE, extra: { readings: [] } }]), null);
});

test("the choice is whichever reading the text is, or the person's own", () => {
  const r = readingsOf([finding])!;
  assert.equal(chosen(" x squared ", r), "ai");
  assert.equal(chosen("x cubed", r), "rule");
  assert.equal(chosen("x to the third", r), "own");
  assert.deepEqual(choiceFor("x cubed", r),
    { formula: "page-003_formula-2.mml", chose: "rule", readings: { ai: "x squared", rule: "x cubed" } });
  assert.equal(differences(r), "numbers: the AI has 2, by rule 3");
});

test("a third reading, from the formula's picture, can be chosen and is described", () => {
  const r = readingsOf([{
    issue: DISAGREE,
    extra: {
      formula: "f", mathml: "<math/>", picture_mathml: "<math/>", differs: null,
      differs_picture: { numbers: { ai_only: ["2"], rule_only: ["3"] }, letters: { ai_only: [], rule_only: [] } },
      readings: [{ source: "ai", label: "AI", text: "x squared" }, { source: "rule", label: "Rule", text: "x squared" },
                 { source: "picture", label: "Picture", text: "x cubed" }],
    },
  }])!;
  assert.equal(chosen("x cubed", r), "picture");
  assert.deepEqual(choiceFor("x cubed", r).readings, { ai: "x squared", rule: "x squared", picture: "x cubed" });
  assert.equal(differences(r), "numbers: the AI's transcription has 2, the picture 3");
});
