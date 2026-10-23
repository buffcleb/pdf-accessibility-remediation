# The audit log to a SIEM

[Operations](README.md) › The audit log to a SIEM

The portal's audit log records sign-ins and failed attempts, code requests (including refused ones), downloads of
other people's documents, every administrator's change, exports, and the
[security alerts](monitoring.md#security-alerts). It's append-only and kept for the records retention period. This
page sends it to the campus's SIEM, and says what the pods' own logs hold for the platform team's log shipping.

## What is sent

Every audit event, as one JSON object, oldest first. **Never a document's content**: the audit log holds file names,
not what's in them.

```json
{"id": 48211, "time": "2026-10-21T14:03:12.418Z", "service": "pdf-remediation", "action": "login",
 "outcome": "invalid_code", "actor": {"id": null, "email": "person@example.edu"},
 "group": {"id": null, "name": null}, "target": {"type": null, "id": null}, "ip": "198.51.100.7",
 "user_agent": "Mozilla/5.0 …", "details": {"attempt": 2}}
```

| Field | |
|---|---|
| `id` | Increases with each event; a collector can use it to spot a repeat after a retry |
| `time` | When it happened, UTC |
| `action` | What happened: `login`, `login.code_requested`, `file.accessed`, `role.granted`, `settings.updated`, `security.alert`, … |
| `outcome` | `success`, or why not: `invalid_code`, `rate_limited`, `bot_check_failed`, `not_provisioned`, `disabled`, `refresh_reuse`, `alert` |
| `actor` | Who: their id and email (an email alone for someone not signed in) |
| `group` | The group it belongs to |
| `target` | What it was done to: a job, a user, a group, a service |
| `ip`, `user_agent` | The caller's address (as the trusted proxies report it) and browser |
| `details` | The specifics: the old and new values of a change, whose document was downloaded and why, an alert's rule and count |

## Choosing how

Set in `portal.env` (Kubernetes: `deploy/k8s/portal.env`, then `scripts/deploy.sh`). In order of preference:

### Syslog over TLS

```text
PORTAL_SIEM_MODE=syslog
PORTAL_SIEM_SYSLOG_HOST=<collector.example.edu>
PORTAL_SIEM_SYSLOG_PORT=6514
```

- RFC 5424 messages over TCP with TLS (RFC 5425 octet-counting framing), which most collectors and SIEMs take with
  no agent. Facility 13 (log audit). Severity: warning for a security alert, notice for anything that didn't
  succeed, info for the rest. APP-NAME `pdf-remediation`, MSGID the action, the message the JSON above.
- The collector's certificate must be trusted: by the usual public CAs, or by its own CA given as
  `PORTAL_SIEM_SYSLOG_CA` (the PEM, with `\n` for line breaks). TLS 1.2 or later; the name is checked.
- `PORTAL_SIEM_SYSLOG_TLS=false` sends plain TCP, for a collector on a trusted network only.
- With `EGRESS_PROXY=enforce`, `deploy.sh` lets the portal reach that port directly (syslog doesn't go through a web
  proxy), as it does for SMTP.

### HTTPS

```text
PORTAL_SIEM_MODE=https
PORTAL_SIEM_HTTPS_URL=https://<siem.example.edu>:8088/services/collector
PORTAL_SIEM_HTTPS_TOKEN=<token>
PORTAL_SIEM_HTTPS_FORMAT=splunk
```

- One POST per batch. `lines` (the default): the events as JSON lines (`application/x-ndjson`) with
  `Authorization: Bearer <token>`, for a generic webhook. `splunk`: Splunk's HTTP Event Collector format, with
  `Authorization: Splunk <token>`.
- The address goes on the egress proxy's list by itself (`deploy/k8s/egress_hosts.py`).

### A pull API

For a SIEM that polls:

```text
PORTAL_SIEM_PULL_TOKEN=<a long random value>
```

```bash
curl -H "Authorization: Bearer <token>" "https://<app host>/api/v1/admin/audit/export?after=0&limit=500"
```

- The same JSON objects, one per line. The `X-Next-After` header is the `after` for the next call; the same value
  back means nothing new.
- The token is for this alone: it can't sign in or call anything else. Without it, the address doesn't exist.
- It works alongside syslog or HTTPS, or on its own (`PORTAL_SIEM_MODE=off`).

`PORTAL_SIEM_BATCH` (500) is how many events go in one message or call.

## How delivery works

- The portal worker sends, about every 30 seconds, in order of `id`, from where it left off. Its place is kept in the
  database, so a restart carries on.
- **Nothing is dropped.** A batch the collector doesn't take is sent again after 30 seconds, then a minute, doubling
  up to 15 minutes; the place only moves once a batch is accepted. A batch that failed part way may arrive twice: the
  `id` tells the copies apart.
- Events are sent once they're two minutes old, so one whose transaction finishes a moment after a later one's isn't
  passed over.
- The first time, it sends the whole log from the start, at a few thousand events a minute.
- **Administration › System** shows where it sends, how far it's got, how long the oldest unsent event has waited,
  and the last error. **More than an hour behind** is an alert to super admins ("The audit log isn't reaching the
  SIEM"), and `/api/v1/status` answers 503 while it lasts.

## The pods' own logs

> **Changed 22 October 2026:** ClamAV and the portal's own Redis added.

The platform team's log shipping (for example Fluent Bit or Vector on the nodes) picks up each pod's standard output.
What's there:

| Pod | Logs |
|---|---|
| `portal-api` | Each request: method, path (with ids, never file contents) and status; errors with their cause |
| `portal-worker` | What went wrong in its passes (a document by id, an email by address), the clean-up's counts, alerts and security alerts, the SIEM export's failures |
| `engine-api`, `engine-worker` | Each job's stages by id and page number, counts, warnings and errors |
| `litellm` | Its requests and errors; not the prompts or answers, unless its verbose logging is turned on |
| `egress-proxy` | Every outside host:port each component reached (never the content: HTTPS is end to end) |
| `web` | nginx's access log: address, path, status |
| `clamav` (with malware scanning on) | clamd: each file found infected, with the signature's name (the portal's audit log has who uploaded it); freshclam: its signature updates |
| `postgres`, `redis`, `redis-portal`, `seaweedfs` | Their own start-up, warnings and errors |

The service doesn't log document text. An error passed on from an outside service is logged as it came back, which
may quote part of what was sent. The engine's detailed log for each job is in its job folder, not on standard output,
and goes with the folder after 24 hours. `PORTAL_MAIL_BACKEND=console` prints whole emails, sign-in codes included,
to the log: it's for local development only.

Mind that the pods' logs hold email addresses and IP addresses: the log shipping's storage needs the same care as
the audit log.

## Related

- [Security alerts](monitoring.md#security-alerts): what's watched for in the audit log.
- [Incident response](incident-response.md): what to do when one fires.
