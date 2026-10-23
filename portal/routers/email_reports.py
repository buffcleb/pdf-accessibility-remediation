"""Email reports: what someone gets and how often, scheduled report views, samples on request, and the unsubscribe
link (no sign-in)."""
from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal import email_reports, live_reports, notifications, report_views, rls, settings_store
from portal.db import get_db
from portal.deps import Principal, current_principal
from portal.errors import not_found
from portal.models import SavedReport, User

router = APIRouter(tags=["email reports"])


class ReportChoice(BaseModel):
    kind: str
    label: str
    description: str
    frequency: str


class ReportChoices(BaseModel):
    enabled: bool  # off for everyone: the choices are kept but nothing is sent
    time_zone: str
    send_hour: int
    reports: list[ReportChoice]


class ChoicesIn(BaseModel):
    reports: dict[str, str] = Field(default_factory=dict)  # {kind: frequency}


class UnsubscribeIn(BaseModel):
    token: str = Field(min_length=10, max_length=400)


class UnsubscribeOut(BaseModel):
    report: str
    email: str
    frequency: str


def _out(db: Session, p: Principal) -> ReportChoices:
    rs = settings_store.load(db)
    return ReportChoices(enabled=rs.email_reports_enabled, time_zone=rs.report_time_zone, send_hour=rs.report_send_hour,
                         reports=[ReportChoice(kind=k, label=email_reports.KINDS[k][0],
                                               description=email_reports.KINDS[k][1], frequency=f)
                                  for k, f in email_reports.choices(db, p.user).items()])


@router.get("/me/email-reports", response_model=ReportChoices)
def my_reports(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _out(db, p)


@router.put("/me/email-reports", response_model=ReportChoices)
def set_my_reports(body: ChoicesIn, request: Request, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    allowed = set(email_reports.eligible(p.user))
    for kind, frequency in body.reports.items():
        if kind in allowed:
            email_reports.set_choice(db, request, p.user, kind, frequency, "settings")
    db.commit()
    return _out(db, p)


class NotificationChoice(BaseModel):
    category: str
    label: str
    description: str
    choice: str
    even_if_seen: bool | None = None  # email even about what they saw in the portal (documents and help only)


class NotificationsOut(BaseModel):
    choices: list[NotificationChoice]


class NotificationsIn(BaseModel):
    choices: dict[str, str] = Field(default_factory=dict)  # {category: each | daily | off}
    even_if_seen: dict[str, bool] = Field(default_factory=dict)  # {category: email even if seen}


class SeenIn(BaseModel):
    # What's open and in view: "job:<id>", "help:<id>", or "jobs" (the documents list)
    items: list[str] = Field(default_factory=list, max_length=50)


def _notifications(db: Session, user: User) -> NotificationsOut:
    return NotificationsOut(choices=[NotificationChoice(
        category=c, label=notifications.CATEGORIES[c][0], description=notifications.CATEGORIES[c][1], choice=v,
        even_if_seen=notifications.even_if_seen(user, c) if c in notifications.SEEN_KINDS else None)
        for c, v in notifications.choices(db, user).items()])


@router.get("/me/notifications", response_model=NotificationsOut)
def my_notifications(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """How often each kind of notification email comes: as it happens, once a day, or not at all."""
    return _notifications(db, p.user)


@router.put("/me/notifications", response_model=NotificationsOut)
def set_my_notifications(body: NotificationsIn, request: Request, p: Principal = Depends(current_principal),
                         db: Session = Depends(get_db)):
    notifications.set_choices(db, request, p.user, body.choices)
    for category, value in body.even_if_seen.items():
        notifications.set_even_if_seen(db, request, p.user, category, value)
    db.commit()
    return _notifications(db, p.user)


@router.post("/me/seen", status_code=204)
def seen(body: SeenIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The app, every half minute while a page is in view: what's open, so emails about it aren't sent."""
    notifications.mark_seen(db, p.user, [i for i in body.items if _valid_item(i)])
    db.commit()


def _valid_item(item: str) -> bool:
    kind, _, rest = item.partition(":")
    if item == "jobs":
        return True
    try:
        return kind in ("job", "help") and bool(uuid.UUID(rest))
    except ValueError:
        return False


@router.post("/me/email-reports/sample", status_code=202)
def send_sample(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Email me my reports for last month now, to see what they look like."""
    subject = email_reports.send_preview(db, p.user)
    db.commit()
    return {"subject": subject, "to": p.user.email}


def _view_of(db: Session, kind: str) -> SavedReport | None:
    return db.get(SavedReport, uuid.UUID(kind[5:])) if kind.startswith("view:") else None


@router.get("/email-reports/unsubscribe", response_model=UnsubscribeOut)
def unsubscribe_info(t: str, request: Request, db: Session = Depends(get_db)):
    """What a link would turn off; changes nothing (mail scanners follow links)."""
    user, kind = email_reports.read_token(db, t)
    rls.as_person(db, user, request)
    if kind.startswith("view:"):
        view = _view_of(db, kind)
        getting = view is not None and str(user.id) in view.recipients
        return UnsubscribeOut(report=view.name if view else "a report that was deleted", email=user.email,
                              frequency=view.frequency if getting else "off")
    return UnsubscribeOut(report=email_reports.KINDS[kind][0], email=user.email,
                          frequency=email_reports.choices(db, user).get(kind, "off"))


@router.post("/email-reports/unsubscribe", response_model=UnsubscribeOut)
def unsubscribe(body: UnsubscribeIn, request: Request, db: Session = Depends(get_db)):
    user, kind = email_reports.read_token(db, body.token)
    rls.as_person(db, user, request)
    if kind.startswith("view:"):
        view = _view_of(db, kind)
        if view is not None:
            report_views.leave(db, request, view, user, "unsubscribe link")
        db.commit()
        return UnsubscribeOut(report=view.name if view else "a report that was deleted", email=user.email, frequency="off")
    email_reports.set_choice(db, request, user, kind, "off", "unsubscribe link")
    db.commit()
    return UnsubscribeOut(report=email_reports.KINDS[kind][0], email=user.email, frequency="off")


# --- scheduled report views ------------------------------------------------------------------------

class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    report: str = Field(max_length=40)
    params: dict[str, str] = Field(default_factory=dict)
    group_id: uuid.UUID | None = None
    frequency: str = "monthly"
    recipients: list[str] = Field(default_factory=list, max_length=200)  # user ids; empty: just me
    attach_csv: bool = False


class ViewChange(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    frequency: str | None = None
    recipients: list[str] | None = Field(None, max_length=200)
    attach_csv: bool | None = None


class Person(BaseModel):
    id: str
    email: str


class ViewOut(BaseModel):
    id: str
    name: str
    report: str
    report_label: str
    params: dict[str, str]
    group_id: str | None
    frequency: str
    attach_csv: bool
    owner: str
    mine: bool
    recipients: list[Person]
    link: str


def _view_out(db: Session, view: SavedReport, me: User) -> ViewOut:
    people = db.scalars(select(User).where(User.id.in_([uuid.UUID(r) for r in view.recipients]))).all()
    report = live_reports.REPORTS.get(view.report)
    path = f"/admin/groups/{view.group_id}/reports" if view.group_id else "/admin/reports"
    return ViewOut(id=str(view.id), name=view.name, report=view.report, report_label=report.label if report else view.report,
                   params=view.params, group_id=str(view.group_id) if view.group_id else None, frequency=view.frequency,
                   attach_csv=view.attach_csv, owner=view.owner.email, mine=view.owner_id == me.id,
                   recipients=[Person(id=str(u.id), email=u.email) for u in sorted(people, key=lambda u: u.email)],
                   link=path + "?" + urlencode({"report": view.report, **view.params}))


@router.get("/me/report-views", response_model=list[ViewOut])
def my_views(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Views I set up, and views others send me."""
    me = str(p.user.id)
    views = [v for v in db.scalars(select(SavedReport).order_by(SavedReport.name))
             if v.owner_id == p.user.id or me in v.recipients]
    return [_view_out(db, v, p.user) for v in views]


@router.get("/me/report-views/recipients", response_model=list[Person])
def view_recipients(report: str, group_id: uuid.UUID | None = None, p: Principal = Depends(current_principal),
                    db: Session = Depends(get_db)):
    """Who could get a view of this report: people with the same access."""
    probe = SavedReport(owner_id=p.user.id, owner=p.user, report=report, group_id=group_id, params={}, recipients=[],
                        name="probe", frequency="monthly")
    if report_views.scope(probe) is False:
        raise not_found("Report")
    return [Person(id=str(u.id), email=u.email) for u in report_views.candidates(db, probe)]


@router.post("/me/report-views", response_model=ViewOut, status_code=201)
def create_view(body: ViewIn, request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    view = report_views.create(db, request, p.user, name=body.name, report=body.report, params=body.params,
                               group_id=body.group_id, frequency=body.frequency, recipients=body.recipients,
                               attach_csv=body.attach_csv)
    db.commit()
    return _view_out(db, view, p.user)


@router.patch("/me/report-views/{view_id}", response_model=ViewOut)
def change_view(view_id: uuid.UUID, body: ViewChange, request: Request, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    view = report_views.get_for(db, view_id, p.user, owner_only=True)
    report_views.update(db, request, view, p.user, body.model_dump(exclude_unset=True))
    db.commit()
    return _view_out(db, view, p.user)


@router.delete("/me/report-views/{view_id}", status_code=204)
def delete_view(view_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    report_views.delete(db, request, report_views.get_for(db, view_id, p.user, owner_only=True), p.user)
    db.commit()


@router.post("/me/report-views/{view_id}/leave", status_code=204)
def leave_view(view_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    report_views.leave(db, request, report_views.get_for(db, view_id, p.user), p.user, "settings")
    db.commit()


@router.post("/me/report-views/{view_id}/sample", status_code=202)
def sample_view(view_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Email me this view for the last period now."""
    subject = report_views.send_sample(db, report_views.get_for(db, view_id, p.user), p.user)
    db.commit()
    return {"subject": subject, "to": p.user.email}
