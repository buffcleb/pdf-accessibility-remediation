# A server, with Docker or Podman

[Deployment](README.md) › A server, with Docker or Podman

One Linux server for a team: the same two Compose stacks as the [local setup](local-docker.md), behind HTTPS, with real
email, backups and restarts.

> [!WARNING]
> Not yet tested end to end. The pieces are the ones used locally; expect to adjust details for your server.

## The server

- Linux, 4 or more CPUs, 16 GB of memory, and disk for the documents you'll keep (inputs and outputs for the retention
  period). A GPU only if the AI models run on the same machine.
- Docker Engine with the Compose plugin, or Podman 4.7+ with `podman compose`.
- A DNS name for the service, e.g. `pdf.example.org`.

## 1. Settings

Follow steps 1 and 3 of the [local setup](local-docker.md#steps) to create `.env` and `.env.portal`, then change:

```
# .env.portal
PORTAL_APP_URL=https://pdf.example.org
PORTAL_COOKIE_SECURE=true
PORTAL_TRUST_PROXY_HEADERS=true
PORTAL_CLIENT_IP_HEADER=x-forwarded-for   # not behind Cloudflare: the address comes from the reverse proxy
PORTAL_GATEWAY_ADMIN_URL=          # empty: super admins see how to reach the gateway's UI, not a link
# real email (see docs/operations/email-and-branding.md), e.g.
PORTAL_MAIL_BACKEND=smtp
PORTAL_SMTP_HOST=smtp.example.org
PORTAL_SMTP_PORT=587
PORTAL_SMTP_STARTTLS=true
PORTAL_SMTP_USERNAME=…
PORTAL_SMTP_PASSWORD=…
PORTAL_MAIL_FROM=PDF Accessibility <no-reply@example.org>
```

Use long random values for every password and key, and keep both files readable only by the account that runs the
stacks (`chmod 600`).

## 2. Don't expose the internals

The Compose files publish a few ports for local convenience. On a server, only the web app should be reachable, and
only through your HTTPS proxy:

- The engine API (8000) and Mailpit (8025) are published on all interfaces by default. Remove those `ports:` entries,
  or bind them to `127.0.0.1` (for example `"127.0.0.1:8000:8000"`), with a `docker-compose.override.yml`.
- Leave out `mailpit` once real email is configured.
- The gateway's UI is already bound to `127.0.0.1:4000`; reach it over SSH (`ssh -L 4000:localhost:4000 server`).
- Bind the web app to `127.0.0.1:8088` too, and let the proxy (below) be the only way in.

## 3. HTTPS in front

Put a reverse proxy in front of the web app on port 8088. With Caddy, which gets and renews certificates by itself:

```
pdf.example.org {
    reverse_proxy 127.0.0.1:8088
    request_body {
        max_size 100MB
    }
}
```

With nginx, proxy to `http://127.0.0.1:8088`, set `client_max_body_size` to your largest upload, turn off request
buffering for uploads (`proxy_request_buffering off`), and allow 300 s timeouts. Pass `X-Forwarded-For` and
`X-Forwarded-Proto`.

**So the audit log records people's addresses, not the proxy's:** tell the web app which proxy to believe, in
`.env.portal` next to the compose file:

```
WEB_TRUSTED_PROXIES=<range>   # the reverse proxy as the web container sees it: Docker's network range
```

Docker gives its networks addresses from the private 172.16 to 172.31 block unless you set your own;
`docker network inspect <network>` shows the subnet to use (a CIDR range such as `192.0.2.0/24`).

Without it, the web app reports its own peer (the reverse proxy) as the caller, so nobody can make up an address
by sending the header themselves.

Alternatively, a [Cloudflare Tunnel](cloudflare.md#a-tunnel-in-front-tested) needs no open ports at all.

## 4. Start, and keep it running

```bash
docker compose up -d --build
```

```bash
docker compose -p portal -f docker-compose.portal.yml --env-file .env.portal up -d --build
```

Add `restart: unless-stopped` to every service in an override file so the stacks come back after a reboot (Docker), or
generate systemd units with `podman generate systemd` / Quadlet (Podman).

## 5. Backups and updates

- Schedule `scripts/backup.sh --all --keep 14 --upload s3://…` nightly (see
  [Backups](../operations/backups-and-migrations.md)).
- To update: `git pull`, back up, then rerun both `up -d --build` commands. The portal applies database migrations on
  start.

## Scaling on one server

Raise `WORKER_CONCURRENCY` (documents processed at once by the engine worker, 2 by default) within the server's
memory and CPUs: each document keeps about one CPU busy and can use a few GB. The portal asks the engine how many
places its workers have, so `PORTAL_ENGINE_MAX_IN_FLIGHT` (4) only needs raising past that if the engine can't be
asked. A long document's per-page work is also shared over `PAGE_WORKERS` processes (3, from
`PAGE_WORKERS_MIN_PAGES`, 50 pages). Beyond one server, use [Kubernetes](kubernetes.md) or [AWS](aws.md).
