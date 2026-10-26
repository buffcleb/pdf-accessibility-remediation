// What the app shows by role (#564): super admins, service admins, group admins. Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { acrossGroups, can, isAdmin, isStaffRole, roleLabel } from "../src/permissions";

const superAdmin = { is_super_admin: true, permissions: [], admin_of: [] };
const service = { is_super_admin: false, is_service_admin: true, admin_of: [],
  permissions: ["groups.all", "groups.manage", "group_admins", "people", "pages", "files.staff", "costs", "help", "queues",
    "queues.items", "documents.rerun_remove", "attention", "system", "reports.all"] };
const groupAdmin = { is_super_admin: false, permissions: [], admin_of: ["g1"] };
const member = { is_super_admin: false, permissions: [], admin_of: [] };

test("super admins may do everything, whatever /me lists", () => {
  for (const p of ["roles", "queues.pause", "grafana", "people.data"] as const) assert.equal(can(superAdmin, p), true);
});

test("service admins may do what /me lists, and nothing above it", () => {
  assert.equal(can(service, "help"), true);
  assert.equal(can(service, "queues.items"), true);
  for (const p of ["roles", "queues.pause", "grafana", "people.data", "groups.disable"] as const) {
    assert.equal(can(service, p), false, p);
  }
  assert.equal(acrossGroups(service), true);
});

test("group admins and members have none of these", () => {
  assert.equal(can(groupAdmin, "groups.all"), false);
  assert.equal(acrossGroups(groupAdmin), false);
  assert.equal(isAdmin(groupAdmin), true);
  assert.equal(isAdmin(member), false);
  assert.equal(isAdmin(service), true);
  assert.equal(can(null, "help"), false);
  // An older API, before #564, sends no permissions
  assert.equal(can({ is_super_admin: false, admin_of: [] }, "help"), false);
});

test("support's roles and the role a person shows", () => {
  assert.equal(isStaffRole("service_admin"), true);
  assert.equal(isStaffRole("super_admin"), true);
  assert.equal(isStaffRole("group_admin"), false);
  assert.equal(roleLabel([{ role: "group_admin" }, { role: "service_admin" }]), "Service admin");
  assert.equal(roleLabel([{ role: "super_admin" }]), "Super admin");
  assert.equal(roleLabel([{ role: "group_admin" }]), null);
});
