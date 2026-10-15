// The divider between the tag viewer's panes. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SIDE_DEFAULT, SPLIT_DEFAULT, clampSplit, loadSide, loadSplit, saveSide, saveSplit, sideAt, sideKey, splitAt, splitKey,
} from "../src/viewer/split";

test("the pointer's place between the panes is the left pane's share, kept within limits", () => {
  assert.equal(splitAt(500, 100, 900), 50);
  assert.equal(splitAt(340, 100, 900), 30);
  assert.equal(splitAt(120, 100, 900), 20);   // never so narrow a pane can't be used
  assert.equal(splitAt(5000, 100, 900), 80);
  assert.equal(splitAt(300, 400, 400), SPLIT_DEFAULT);
});

test("the keyboard moves it too", () => {
  assert.equal(splitKey("ArrowLeft", 50), 48);
  assert.equal(splitKey("ArrowRight", 79), 80);
  assert.equal(splitKey("Home", 50), 20);
  assert.equal(splitKey("End", 50), 80);
  assert.equal(splitKey("Enter", 33), 50);
  assert.equal(splitKey("a", 50), null);
});

test("it is kept in the browser, and anything odd there is the default", () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  assert.equal(loadSplit(storage), 50);
  saveSplit(storage, 64.4);
  assert.equal(loadSplit(storage), 64);
  store.set("pv-split", "wide");
  assert.equal(loadSplit(storage), 50);
  assert.equal(loadSplit(null), 50);
  assert.equal(loadSplit({ getItem: () => { throw new Error("blocked"); } }), 50);
  assert.equal(clampSplit(NaN), 50);
});

test("the editing panel's width follows the pointer at its left edge, within limits", () => {
  assert.equal(sideAt(1105, 1500), 390);
  assert.equal(sideAt(1400, 1500), 260);   // never too narrow for its fields
  assert.equal(sideAt(100, 1500), 720);    // nor so wide the pages have no room
});

test("the left arrow widens the panel, being on the divider's right", () => {
  assert.equal(sideKey("ArrowLeft", 340), 356);
  assert.equal(sideKey("ArrowRight", 340), 324);
  assert.equal(sideKey("Enter", 500), SIDE_DEFAULT);
  assert.equal(sideKey("Home", 340), 720);
  assert.equal(sideKey("End", 340), 260);
  assert.equal(sideKey("x", 340), null);
});

test("the panel's width is kept apart from the panes' divider", () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  saveSide(storage, 480);
  saveSplit(storage, 30);
  assert.equal(loadSide(storage), 480);
  assert.equal(loadSplit(storage), 30);
  assert.equal(loadSide(null), SIDE_DEFAULT);
});
