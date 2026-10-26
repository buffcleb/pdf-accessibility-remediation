"""What each admin role may do (#564), named once so routes ask for a permission rather than a role.

Super admins may do everything, including whatever is added here later. Service admins help run every group:
they have the permissions listed for them and nothing else. Group admins have none of these: what they may do is
scoped to the groups they administer (`Principal.can_admin_group`). An API token never has any (portal/deps.py).
"""
from __future__ import annotations

SUPER_ADMIN = "super_admin"
SERVICE_ADMIN = "service_admin"
GROUP_ADMIN = "group_admin"

# Every group, as its admins see it: the workspace, members, settings, services, API tokens, Needs attention
ALL_GROUPS = "groups.all"
# Create and rename groups; their email domains and the people allowed by address
MANAGE_GROUPS = "groups.manage"
# Disable a group (signs its people out) or enable it again
DISABLE_GROUPS = "groups.disable"
# Make and remove group admins
GROUP_ADMINS = "group_admins"
# Everyone across groups: the list, signing someone out everywhere, moving someone to another group
PEOPLE = "people"
# Export or remove a person's data (#338)
PERSON_DATA = "people.data"
# Make and remove super admins and service admins
ROLES = "roles"
# Group page pools: top-ups, overrides without a ticket, adjustments, and corrections to a person's usage
PAGES = "pages"
# The engine's own reports, and documents already deleted (their records stay for reports)
STAFF_FILES = "files.staff"
# Dollar costs: of documents, AI descriptions, and in reports
COSTS = "costs"
# Help requests as support: every request, internal notes, assigning, status, corrected files, pages back
HELP = "help"
# The queues and what's in them
QUEUES = "queues"
# One item in a queue: retry, cancel, stop, restart, remove, process now
QUEUE_ITEMS = "queues.items"
# Pause and resume a whole queue
PAUSE_QUEUES = "queues.pause"
# Rerun a document, and remove documents' files at once
RERUN_REMOVE = "documents.rerun_remove"
# Needs attention across every group and service, and the access check for anyone
ATTENTION = "attention"
# Administration > System: figures, charts, pods, the audit export's status
SYSTEM = "system"
# Signing in to Grafana (as its admin) through the portal
GRAFANA = "grafana"
# Every report and column, for every group, and the whole audit log
REPORTS = "reports.all"

SERVICE_ADMIN_PERMISSIONS = frozenset({ALL_GROUPS, MANAGE_GROUPS, GROUP_ADMINS, PEOPLE, PAGES, STAFF_FILES, COSTS, HELP,
                                       QUEUES, QUEUE_ITEMS, RERUN_REMOVE, ATTENTION, SYSTEM, REPORTS})
EVERY = frozenset({ALL_GROUPS, MANAGE_GROUPS, DISABLE_GROUPS, GROUP_ADMINS, PEOPLE, PERSON_DATA, ROLES, PAGES,
                   STAFF_FILES, COSTS, HELP, QUEUES, QUEUE_ITEMS, PAUSE_QUEUES, RERUN_REMOVE, ATTENTION, SYSTEM,
                   GRAFANA, REPORTS})


def of(user) -> frozenset[str]:
    """The permissions this person's roles give them."""
    if user.is_super_admin:
        return EVERY
    if user.is_service_admin:
        return SERVICE_ADMIN_PERMISSIONS
    return frozenset()


def has(user, permission: str) -> bool:
    return user.is_super_admin or permission in of(user)
