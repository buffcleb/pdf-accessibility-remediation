# Security and privacy

[Architecture](README.md) › Security and privacy

## Sign-in and sessions

- **Emailed one-time codes:** 6 digits, valid 10 minutes, a limited number of attempts, only an HMAC stored. Code
  requests and attempts are rate-limited per address and per IP address. (SAML is designed and planned; see the
  [portal spec](../design/portal-spec.md).)
- **Access tokens** are short-lived JWTs (ES256, 10 minutes, rotatable keys), kept in memory, never in localStorage.
- **Refresh tokens** are random, stored only as a hash, rotated on every use (reusing an old one revokes the
  session), and travel in an `HttpOnly; Secure; SameSite=Strict` cookie scoped to the refresh endpoint.
- Sessions time out when idle and after an absolute limit, both configurable.
- **Roles are checked on every endpoint**, and a resource's group is always taken from the database, never from the
  request.
- A break-glass command issues a sign-in code when email fails; each use is audited, never the code.

## Network

- Only the web app (and the portal API behind it) is public. **The engine and the AI gateway are internal:** only the
  portal calls the engine, with an API key.
- The gateway's admin UI belongs behind a VPN or SSO in any deployment.
- The web app sends a strict Content Security Policy (no inline script, `connect-src 'self'`, `frame-ancestors
  'none'`), and the usual security headers.

## Files

- Files live in object storage, **encrypted at rest**, under keys made of IDs (no names or emails in object names).
- They're served only through the API after a permission check. **An admin opening someone else's file is audited.**
- Retention is set by super admins: files are deleted after a set number of days, records after a longer one.
- Uploads are checked (PDF signature, size, page count, no encryption) before anything else touches them.

## Audit

Sign-ins and code requests, uploads, downloads by others, every quota, limit, grant and service change, settings,
queue actions, help requests and tag edits are recorded, with the group attached so group admins see their own. Report
exports are audited too.

## Where documents go

- With the Standard service and a local AI level, **documents stay on the service's own servers.**
- Services and AI levels that use an outside provider are marked **external**; they can show a notice at upload, can
  ask for confirmation before sending, and are **blocked for local-only groups**.
- The kind-of-document classifier sends only the first pages' text (never images), and only uses an outside service
  for groups that aren't local-only.
- Check each provider's data-retention terms before enabling it. Avoid free model endpoints for real documents: they
  may log prompts.

## Accessibility of the portal itself

The portal targets WCAG 2.2 AA: keyboard operation, visible focus, labeled fields, live regions for updates, charts
with an accessible table of every value, and a contrast check on the configurable accent color. The PDF report it
produces passes PDF/UA-1.
