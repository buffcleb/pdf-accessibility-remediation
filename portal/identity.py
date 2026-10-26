"""Email normalization, domain matching and just-in-time account provisioning."""
from __future__ import annotations

from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.config import get_portal_settings
from portal.errors import ApiError
from portal.models import Group, GroupDomain, GroupEmail, RoleAssignment, User


def normalize_email(raw: str) -> str:
    try:
        info = validate_email(raw.strip(), check_deliverability=False)
    except EmailNotValidError:
        raise ApiError(422, "invalid_email", "Enter a valid email address.") from None
    return info.normalized.lower()


def lookup_email(raw: str) -> str:
    """For finding someone who already has an account: the normalized address, or, if it fails today's
    validation (an older or test address), the address as stored, lower-cased. Never used to create accounts."""
    try:
        return normalize_email(raw)
    except ApiError:
        return raw.strip().lower()


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[1]


def normalize_domain(raw: str) -> str:
    """Lower-case, IDNA-encoded hostname. Raises ApiError if it isn't a plausible domain."""
    d = raw.strip().lower().rstrip(".")
    if d.startswith("@"):
        d = d[1:]
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError:
        raise ApiError(422, "invalid_domain", "Enter a domain such as example.edu.") from None
    labels = d.split(".")
    ok = (
        len(d) <= 253 and len(labels) >= 2
        and all(0 < len(lbl) <= 63 and lbl.replace("-", "").isalnum() and not lbl.startswith("-")
                and not lbl.endswith("-") for lbl in labels)
    )
    if not ok:
        raise ApiError(422, "invalid_domain", "Enter a domain such as example.edu.")
    return d


@dataclass
class Eligibility:
    """Who an email address would sign in as."""
    user: User | None          # existing account
    group: Group | None        # group a new account would join
    bootstrap: bool            # new account becomes a super admin
    by_address: bool = False   # the group lists this address (rather than its domain)

    @property
    def allowed(self) -> bool:
        return self.user is not None or self.group is not None or self.bootstrap


def eligibility(db: Session, email: str) -> Eligibility:
    """An existing account; else the group that lists this address; else the group of its domain; else nothing
    (or a bootstrap super admin). Addresses and domains are each listed on one group only and matched exactly, so
    a subgroup's (#431: an address, or its own domain such as nursing.example.edu) wins over its parent's domain."""
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        return Eligibility(user, user.group, False)
    bootstrap = email in get_portal_settings().bootstrap_emails
    # An address listed on a group comes first: it can place one person apart from the rest of their domain
    listed = db.scalar(select(Group).join(GroupEmail).where(GroupEmail.email == email, Group.status == "active"))
    if listed is not None:
        return Eligibility(None, listed, bootstrap, by_address=True)
    group = db.scalar(
        select(Group).join(GroupDomain).where(GroupDomain.domain == domain_of(email), Group.status == "active"))
    return Eligibility(None, group, bootstrap)


def provision(db: Session, email: str, auth_method: str, elig: Eligibility, name: str | None = None) -> User:
    """Create the account. Its group is fixed from here on (accounts are per email address)."""
    from portal.approval import starts_pending
    user = User(email=email, name=name, group_id=elig.group.id if elig.group else None, auth_method=auth_method,
                approval="pending" if starts_pending(elig.group, elig.bootstrap) else "approved")
    db.add(user)
    db.flush()
    if elig.bootstrap:
        db.add(RoleAssignment(user_id=user.id, role="super_admin"))
        db.flush()
        db.refresh(user)
    return user
