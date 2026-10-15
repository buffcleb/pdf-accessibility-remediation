"""Outgoing email, through a provider chosen by PORTAL_MAIL_BACKEND:

  smtp        any SMTP relay (an organization's own, Mailpit locally, or Cloudflare's SMTP submission)
  ses         Amazon SES, through its API (credentials from the environment or the machine's role)
  plunk       Plunk, through its HTTP API
  cloudflare  Cloudflare Email Service, through its REST API (sender address only: Cloudflare's REST docs
              show no display name; use its SMTP submission with the smtp backend to send one). Attachments
              are sent; a message can be 5 MiB in all
  console     logged, not sent (local demos)
  memory      kept in memory (tests)

Secrets come only from the environment. Temporary failures (timeouts, 429, 5xx) are retried with backoff;
a mail failure never blocks job processing, and sign-in reports it plainly.
"""
from __future__ import annotations

import base64
import logging
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from functools import lru_cache

import httpx

from portal.config import get_portal_settings

log = logging.getLogger(__name__)

BACKENDS = ("smtp", "ses", "plunk", "cloudflare", "console", "memory")


@dataclass
class Mail:
    to: str
    subject: str
    text: str
    html: str | None = None  # an HTML version (scheduled reports); the text is always sent too
    # (filename, content type, bytes): sent by the backends in ATTACHMENTS
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)
    message_id: str | None = None  # set on sending: our Message-ID, or the provider's own ID for the message


# Backends that can send attachments; with the others, emails say where to download the file instead
ATTACHMENTS = {"smtp", "ses", "cloudflare", "console", "memory"}
# Cloudflare's REST API takes a message of 5 MiB in all, attachments included, and they travel as base64 (a third
# larger). This is what attachments may add up to before encoding, leaving room for the text and HTML.
CLOUDFLARE_MESSAGE_BYTES = 5 * 1024 * 1024
CLOUDFLARE_ATTACHMENT_BYTES = 3_400_000


def attachment_limit(backend: str | None = None) -> int | None:
    """The most bytes of attachments one message can carry with this backend (None: no limit of the backend's;
    0: it can't send attachments). Callers leave out what doesn't fit and say where to download it."""
    backend = backend or get_portal_settings().mail_backend
    if backend not in ATTACHMENTS:
        return 0
    return CLOUDFLARE_ATTACHMENT_BYTES if backend == "cloudflare" else None


def _mime(mail: Mail, name: str, address: str) -> EmailMessage:
    """The whole message: text, an HTML alternative and attachments."""
    s = get_portal_settings()
    msg = EmailMessage()
    msg["From"] = formataddr((name, address)) if name else address
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    # Always set: senders such as Cloudflare sign "date" even when it's missing, so a receiving server that adds
    # one (Microsoft does) breaks the DKIM signature
    msg["Date"] = formatdate(usegmt=True)
    mail.message_id = mail.message_id or make_msgid(domain=address.rpartition("@")[2] or None)
    msg["Message-ID"] = mail.message_id
    if s.mail_reply_to:
        msg["Reply-To"] = s.mail_reply_to
    msg.set_content(mail.text)
    if mail.html:
        msg.add_alternative(mail.html, subtype="html")
    for filename, content_type, data in mail.attachments:
        maintype, _, subtype = content_type.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype.split(";")[0] or "octet-stream", filename=filename)
    return msg


class MailError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def sender(from_name: str | None = None) -> tuple[str, str]:
    """(display name, address): PORTAL_MAIL_FROM, with the branding's sender name if one is set."""
    name, address = parseaddr(get_portal_settings().mail_from)
    return (from_name or name or "").strip(), address


def _http_error(provider: str, r: httpx.Response) -> MailError:
    detail = r.text[:300]
    return MailError(f"{provider} answered HTTP {r.status_code}: {detail}", retryable=r.status_code == 429 or r.status_code >= 500)


# --- backends ----------------------------------------------------------------------------------------

def _smtp(mail: Mail, name: str, address: str) -> None:
    s = get_portal_settings()
    msg = _mime(mail, name, address)
    context = ssl.create_default_context()
    try:
        server = (smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=20) if s.smtp_ssl
                  else smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20))
        with server:
            if s.smtp_starttls and not s.smtp_ssl:
                server.starttls(context=context)
            if s.smtp_username:
                server.login(s.smtp_username, s.smtp_password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"SMTP sign-in was refused ({e.smtp_code})") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError("the SMTP server refused the recipient") from e
    except smtplib.SMTPResponseException as e:
        raise MailError(f"SMTP error {e.smtp_code}", retryable=400 <= e.smtp_code < 500) from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP server unreachable or failed: {e}", retryable=True) from e


def _ses(mail: Mail, name: str, address: str) -> None:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    s = get_portal_settings()
    client = boto3.client("sesv2", region_name=s.ses_region or None)
    args = {
        "FromEmailAddress": formataddr((name, address)) if name else address,
        "Destination": {"ToAddresses": [mail.to]},
        "Content": {"Simple": {"Subject": {"Data": mail.subject, "Charset": "UTF-8"},
                               "Body": {"Text": {"Data": mail.text, "Charset": "UTF-8"},
                                        **({"Html": {"Data": mail.html, "Charset": "UTF-8"}} if mail.html else {})}}},
    }
    if mail.attachments:  # SES's simple form has no attachments: send the whole MIME message
        args["Content"] = {"Raw": {"Data": _mime(mail, name, address).as_bytes()}}
    if s.mail_reply_to:
        args["ReplyToAddresses"] = [s.mail_reply_to]
    if s.ses_configuration_set:
        args["ConfigurationSetName"] = s.ses_configuration_set
    try:
        resp = client.send_email(**args)
        mail.message_id = resp.get("MessageId") or mail.message_id  # SES's ID, as its logs show it
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        raise MailError(f"SES refused the message ({code})",
                        retryable=code in ("TooManyRequestsException", "ThrottlingException",
                                           "InternalFailure", "ServiceUnavailable")) from e
    except BotoCoreError as e:
        raise MailError(f"SES unreachable: {e}", retryable=True) from e


def _plunk(mail: Mail, name: str, address: str) -> None:
    s = get_portal_settings()
    if not s.plunk_api_key:
        raise MailError("PORTAL_PLUNK_API_KEY isn't set")
    body = {"to": mail.to, "subject": mail.subject, "body": mail.html or _text_as_html(mail.text),
            "from": {"name": name, "email": address} if name else address, "subscribed": False}
    if s.mail_reply_to:
        body["reply"] = s.mail_reply_to
    try:
        r = httpx.post(f"{s.plunk_base_url.rstrip('/')}/v1/send", json=body, timeout=20,
                       headers={"Authorization": f"Bearer {s.plunk_api_key}"})
    except httpx.HTTPError as e:
        raise MailError(f"Plunk unreachable: {e}", retryable=True) from e
    if r.status_code >= 300:
        raise _http_error("Plunk", r)
    try:
        emails = (r.json() or {}).get("emails") or []
        mail.message_id = str(emails[0].get("id")) if emails and emails[0].get("id") else mail.message_id
    except (ValueError, AttributeError):
        pass


def _cloudflare(mail: Mail, name: str, address: str) -> None:
    s = get_portal_settings()
    if not (s.cloudflare_account_id and s.cloudflare_api_token):
        raise MailError("PORTAL_CLOUDFLARE_ACCOUNT_ID and PORTAL_CLOUDFLARE_API_TOKEN must be set")
    body = {"to": mail.to, "from": address, "subject": mail.subject, "text": mail.text}
    if mail.html:
        body["html"] = mail.html
    if s.mail_reply_to:
        body["reply_to"] = s.mail_reply_to
    if mail.attachments:
        body["attachments"] = [{"content": base64.b64encode(data).decode("ascii"), "filename": filename,
                                "type": content_type.split(";")[0].strip() or "application/octet-stream",
                                "disposition": "attachment"}
                               for filename, content_type, data in mail.attachments]
        size = sum(len(a["content"]) for a in body["attachments"]) + len(mail.text.encode()) + len((mail.html or "").encode())
        if size > CLOUDFLARE_MESSAGE_BYTES:
            raise MailError("the message is larger than Cloudflare's 5 MiB limit with its attachments")
    url = f"https://api.cloudflare.com/client/v4/accounts/{s.cloudflare_account_id}/email/sending/send"
    try:
        r = httpx.post(url, json=body, timeout=20, headers={"Authorization": f"Bearer {s.cloudflare_api_token}"})
    except httpx.HTTPError as e:
        raise MailError(f"Cloudflare unreachable: {e}", retryable=True) from e
    if r.status_code >= 300:
        raise _http_error("Cloudflare", r)
    result = (r.json() or {}).get("result") or {}
    if mail.to in (result.get("permanent_bounces") or []):
        raise MailError("Cloudflare reports the address bounces permanently")
    mail.message_id = str(result.get("message_id") or result.get("id") or "") or mail.message_id


def _text_as_html(text: str) -> str:
    import html
    return "<div style=\"font-family: sans-serif; white-space: pre-wrap\">" + html.escape(text) + "</div>"


_SEND = {"smtp": _smtp, "ses": _ses, "plunk": _plunk, "cloudflare": _cloudflare}


class Mailer:
    def __init__(self) -> None:
        self.outbox: list[Mail] = []  # memory backend only
        self.sleep = time.sleep

    def send(self, mail: Mail, from_name: str | None = None, attempts: int | None = None) -> bool:
        """Send, retrying temporary failures. Returns whether it was sent; failures are logged, not raised."""
        try:
            self.deliver(mail, from_name, attempts)
            return True
        except MailError as e:
            log.error("sending mail to %s failed: %s", mail.to, e)
            return False

    def deliver(self, mail: Mail, from_name: str | None = None, attempts: int | None = None) -> None:
        """Send, retrying temporary failures; raises MailError with the last failure."""
        s = get_portal_settings()
        if s.mail_backend in ("memory", "console"):  # nothing really sent: still an ID, so the log looks as it will
            mail.message_id = mail.message_id or make_msgid(domain="localhost")
        if s.mail_backend == "memory":
            self.outbox.append(mail)
            return
        if s.mail_backend == "console":
            log.warning("mail (console backend, not sent) to=%s subject=%s\n%s", mail.to, mail.subject, mail.text)
            return
        send = _SEND.get(s.mail_backend)
        if send is None:
            raise MailError(f"unknown mail backend {s.mail_backend!r}; use one of {', '.join(BACKENDS)}")
        name, address = sender(from_name)
        tries = attempts or s.mail_attempts
        for attempt in range(1, tries + 1):
            try:
                send(mail, name, address)
                return
            except MailError as e:
                if not e.retryable or attempt == tries:
                    raise
                wait = min(30.0, 2.0 ** attempt)
                log.warning("mail to %s failed (%s), retry %d in %.0fs", mail.to, e, attempt, wait)
                self.sleep(wait)


@lru_cache
def get_mailer() -> Mailer:
    return Mailer()


def sign_in_code_mail(to: str, code: str, minutes: int, service_name: str = "the PDF remediation service") -> Mail:
    s = get_portal_settings()
    return Mail(
        to=to,
        # Never the code in the subject: subjects are kept in mail logs, message traces and lock-screen previews
        subject=f"Your sign-in code for {service_name}",
        text=(
            f"Your sign-in code for {service_name} is:\n\n    {code}\n\n"
            f"It expires in {minutes} minutes and can be used once.\n\n"
            f"If you didn't try to sign in at {s.app_url}, you can ignore this email.\n"
        ),
    )


def test_mail(to: str, service_name: str) -> Mail:
    s = get_portal_settings()
    return Mail(to=to, subject=f"Test email from {service_name}",
                text=(f"This is a test email from {service_name} ({s.app_url}), sent with the "
                      f"{s.mail_backend} mail backend. If you received it, email is working.\n"))
