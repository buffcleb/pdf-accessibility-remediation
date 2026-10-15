// What the "AI gateway" dialog tells a super admin, against the operations guide it links to. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { GATEWAY_HELP as help } from "../src/pages/admin/gatewayHelp";

const guide = readFileSync(`../${help.guidePath}`, "utf8");   // npm test runs in web/

test("the command and the address in the dialog are the guide's", () => {
  assert.ok(guide.includes(help.command.replace("NAMESPACE", "NS")), "the port-forward command");
  assert.ok(guide.includes(help.address), "the address to open");
  assert.ok(guide.includes(help.otherPort), "the command for another local port");
});

test("the link goes to that guide and to a heading it has", () => {
  assert.ok(help.guideUrl.includes(`/${help.guidePath}#`));
  assert.ok(guide.includes(`## ${help.guideHeading}\n`));
  assert.equal(help.guideUrl.split("#")[1], help.guideHeading.toLowerCase().replace(/ /g, "-"));
});
