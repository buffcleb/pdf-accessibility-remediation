# Email and branding

[Operations](README.md) › Email and branding

## Email

The portal emails sign-in codes, notifications (documents done, help requests, approvals) and scheduled reports. The
provider is chosen with `PORTAL_MAIL_BACKEND`; secrets come only from the environment.

| Backend | Use | Settings |
|---|---|---|
| `smtp` | Any SMTP relay: your organization's, Mailpit locally, or Cloudflare Email Service's SMTP submission | `PORTAL_SMTP_HOST`, `_PORT`, `_STARTTLS` or `_SSL`, `_USERNAME`, `_PASSWORD` |
| `ses` | Amazon SES through its API | `PORTAL_SES_REGION`, optional `PORTAL_SES_CONFIGURATION_SET`; credentials from the AWS environment or the machine's role |
| `plunk` | Plunk's HTTP API | `PORTAL_PLUNK_API_KEY` (secret key), `PORTAL_PLUNK_BASE_URL` |
| `cloudflare` | Cloudflare Email Service's REST API | `PORTAL_CLOUDFLARE_ACCOUNT_ID`, `PORTAL_CLOUDFLARE_API_TOKEN`. Attachments are sent (a message can be 5 MiB in all). No sender name; for that, use Cloudflare's SMTP submission with the `smtp` backend (below) |
| `console` | Local demos: logged, not sent | – |

**Cloudflare Email Service over SMTP** (sender names and attachments work):

```
PORTAL_MAIL_BACKEND=smtp
PORTAL_SMTP_HOST=smtp.mx.cloudflare.net
PORTAL_SMTP_PORT=465
PORTAL_SMTP_SSL=true
PORTAL_SMTP_USERNAME=api_token
PORTAL_SMTP_PASSWORD=<a Cloudflare API token allowed to send email>
```

**Attachments.** Scheduled report views can attach a CSV. The `smtp`, `ses` and `cloudflare` backends send it; with
`plunk` the email says to download it from the app. A CSV over 5 MB isn't attached (over about 3.4 MB with
`cloudflare`, whose messages can be 5 MiB in all once the file is encoded); the email says to download it instead.

- **Sender:** `PORTAL_MAIL_FROM` gives the address (on a domain the provider has verified) and a default name. The
  name can be changed in **Administration → Settings → Branding**. `PORTAL_MAIL_REPLY_TO` is optional.
- **Every message has its own `Date` and `Message-ID`.** (Some providers sign `Date` even when it's missing, so a
  receiving server that adds one would break the DKIM signature.)
- **Failures:** temporary ones (timeouts, throttling, server errors) are retried up to `PORTAL_MAIL_ATTEMPTS` times with
  backoff. If a sign-in code can't be sent, the person is told at once, rather than waiting for a code that never
  comes. A failed notification never holds up processing; it waits in the Email queue's Failed list.
- **Test it:** **Administration → Settings → Email → Send test email** shows which provider is in use and whether the
  message was accepted. Every test is in the audit log.

### Domain authentication

So codes don't land in spam, set up the sending domain at its DNS host before going live, following the provider's
instructions:

- **SPF:** a TXT record that lists the provider (e.g. `include:amazonses.com`), or the provider's own record.
- **DKIM:** the provider's signing keys, as CNAME or TXT records.
- **DMARC:** a TXT record at `_dmarc.<domain>`. Start with `v=DMARC1; p=none; rua=mailto:<reports address>`, then move
  to `quarantine` or `reject` once reports show everything passes.

Check with a real inbox: send the test email, then "show original" (Gmail) or "message details" (Outlook): SPF, DKIM
and DMARC should say PASS. A new sending domain may still land in junk at first, until it has a sending history;
asking your organization's mail administrators to allow the sender helps.

**Not covered yet:** bounce and complaint handling (provider webhooks), so the service would stop emailing addresses
that bounce.

### When sign-in codes don't arrive

First look in [the email log](#the-email-log) (filter **Kind: Sign-in code** and search the address): it says
whether the code was handed to the mail provider, when, with which message ID, or the error if it wasn't. With the
message ID you can find it in the provider's logs (Cloudflare, SES) or ask the recipient's mail team to trace it.

`scripts/sign-in-code.sh` issues a new sign-in code and prints it, for when email isn't working:

```bash
scripts/sign-in-code.sh person@example.org --reason "mail outage"
```

Add `--k8s` for a Kubernetes deployment. It applies the same checks as the sign-in page (the address must be allowed
to sign in, the account not disabled); the code works once, replaces any code sent before, and expires as usual. The
portal stores only a hash of each code, so a mailed code can't be read back. Each use is in the audit log
(`login.code_issued`, with who ran it and why), never the code. Pass it on privately.

### The email log

**Reports → Activity → Email** lists every email the service sent or tried to send: sign-in codes, document and
help notifications, daily summaries, email reports and test emails. Each row has when, to whom, the kind, the
subject, the status (sent, failed, waiting or retrying, held while email is paused, not sent because the person had
already seen it in the portal, combined into a daily summary), the last error, the backend, and (in **Columns**) the
**message ID**: the message's `Message-ID` header, or the provider's own ID (SES, Cloudflare), to find the same email
in the provider's logs or in the recipient's mail trace. Search takes an address, a subject or a message ID.

Sign-in codes and test emails are recorded without their body, so the code is never stored. Other emails keep what
they said for 30 days (so a failed one can be retried); after that only the record stays, for the records retention
period. Super admins see every email; group admins see emails to their group's people.

## Branding

Super admins set the service name, the organization name, a logo (PNG, JPEG or WebP, up to 512 KB, with alt text), an
accent color and the email sender name in **Administration → Settings → Branding**, and the help email address and
page-request link in **Settings → Help and messages**. They appear in the header, on the sign-in page, in the footer,
in page titles, in emails and on the PDF report (names and accent color; the logo isn't on the report yet).

- **Accent color:** used for links and buttons with white text, so it must have at least 4.5:1 contrast against white
  (WCAG AA). It applies to the light theme; dark mode keeps its own accent.
- **Logo:** SVG isn't accepted, because an SVG served from the app could carry script.
- **Defaults:** `config.json` in the web image sets the names a deployment starts with, before anyone changes them in
  the app.
