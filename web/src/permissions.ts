/** What the signed-in person's roles let them do (#564), as the API's portal/permissions.py names it. Super admins
 * may do everything; service admins have the permissions /me lists; group admins have none of these, only their
 * groups (`admin_of`). The API checks every request: this only decides what the app shows. */
export type Permission =
  | "groups.all" | "groups.manage" | "groups.disable" | "group_admins" | "people" | "people.data" | "roles" | "pages"
  | "files.staff" | "costs" | "help" | "queues" | "queues.items" | "queues.pause" | "documents.rerun_remove"
  | "attention" | "system" | "grafana" | "reports.all";

type Who = { is_super_admin: boolean; is_service_admin?: boolean; permissions?: string[]; admin_of: string[] };

export function can(me: Who | null | undefined, permission: Permission): boolean {
  return !!me && (me.is_super_admin || (me.permissions ?? []).includes(permission));
}

/** Works in every group: a super admin or a service admin. */
export function acrossGroups(me: Who | null | undefined): boolean {
  return can(me, "groups.all");
}

/** Has an Administration area: across groups, or an admin of a group. */
export function isAdmin(me: Who | null | undefined): boolean {
  return !!me && (acrossGroups(me) || me.admin_of.length > 0);
}

/** Support's roles in a help request: they see every request and its internal notes. */
export const STAFF_ROLES = ["super_admin", "service_admin"];

export function isStaffRole(role: string | null | undefined): boolean {
  return !!role && STAFF_ROLES.includes(role);
}

/** The role a person's account shows, highest first. */
export function roleLabel(roles: { role: string }[]): string | null {
  if (roles.some((r) => r.role === "super_admin")) return "Super admin";
  if (roles.some((r) => r.role === "service_admin")) return "Service admin";
  return null;
}
