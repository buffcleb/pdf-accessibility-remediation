// The page number box in the tag manager's one-page view. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { typedPage } from "../src/viewer/pageBox";

test("a page number goes to that page", () => {
  assert.equal(typedPage("5", 332), 5);
  assert.equal(typedPage(" 12 ", 332), 12);
  assert.equal(typedPage("007", 332), 7);
});

test("a number out of range goes to the first or last page", () => {
  assert.equal(typedPage("0", 332), 1);
  assert.equal(typedPage("999", 332), 332);
  assert.equal(typedPage("3", 0), 1);    // pages not counted yet
});

test("anything else isn't a page", () => {
  for (const t of ["", "  ", "abc", "-3", "2.5", "1e2", "5a"]) assert.equal(typedPage(t, 332), null, t);
});
