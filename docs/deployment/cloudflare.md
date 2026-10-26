# Cloudflare

[Deployment](README.md) › Cloudflare

Three ways to use Cloudflare, from a tunnel in front of a deployment elsewhere to running more of the service on
Cloudflare itself.

| Option | What runs on Cloudflare | Status |
|---|---|---|
| [A tunnel in front](#a-tunnel-in-front-tested) | Nothing but the edge: the service runs on your own servers or cluster | **Tested** (the reference deployment) |
| [The web app on a Worker](#the-web-app-on-a-cloudflare-worker) | The web app's files, served from Cloudflare's edge; the API stays on your servers | Tested locally (`wrangler dev`); not yet in production |
| [More of it on Cloudflare](#running-more-of-it-on-cloudflare) | Web app, file storage (R2), email, possibly the containers | A design, not yet tested |

## A tunnel in front (tested)

A Cloudflare Tunnel connects your deployment to Cloudflare's edge from the inside (`cloudflared` dials out), so there
are no inbound ports or public IP, and TLS ends at Cloudflare.

**On Kubernetes** (bundled):

1. In Cloudflare Zero Trust → Networks → Tunnels, create a tunnel (cloudflared), and add a public hostname:
   `pdf.example.org` → HTTP → `web.pdf-remediation.svc.cluster.local:8080`. Cloudflare creates the DNS record.
2. Put the tunnel's token in `deploy/k8s/tunnel.env` as `TUNNEL_TOKEN=…` (git-ignored).
3. `scripts/deploy.sh` runs two `cloudflared` replicas in the namespace.

**With Docker:** run `cloudflared` next to the portal stack, routing the hostname to `http://portal-web:8080` (on the
same Compose network) or `http://localhost:8088`.

**Settings:** behind Cloudflare, the caller's address is in `cf-connecting-ip`, which the portal reads by default
(`PORTAL_TRUST_PROXY_HEADERS=true`). Uploads through Cloudflare are capped by its plan (100 MB on Free and Pro), so
keep `MAX_UPLOAD_SIZE` and the portal's largest file at or below that.

## A bot check on sign-in

Every "Email me a code" sends an email, so a script could use the sign-in page to send mail to any address, at your
cost and your sender reputation's. Rate limits slow that down; **Turnstile**, Cloudflare's free bot check, stops it.
Most people never see it: it runs in the background and only shows a challenge when Cloudflare needs one.

1. In the Cloudflare dashboard: **Turnstile → Add widget**. Name it, add your sign-in hostname (`APP_HOST`, e.g.
   `pdf.example.org`; add `localhost` too if you test locally), and choose **Managed** mode.
2. Put the two keys in the portal's settings (`deploy/k8s/portal.env`, or `.env.portal`):
   `PORTAL_TURNSTILE_SITE_KEY=…` and `PORTAL_TURNSTILE_SECRET_KEY=…`. Redeploy.
3. Check: the sign-in page says "Check complete." under the button, and a code arrives.

The site key is public (the page needs it); the secret key isn't. If Cloudflare's check can't be reached, sign-in is
allowed by default and the audit log notes it (`login.code_requested` with "bot_check: unavailable (allowed)");
`PORTAL_TURNSTILE_FAIL_OPEN=false` refuses instead. Refused requests are in the audit log with the outcome
`bot_check_failed`. The page's content security policy already allows `challenges.cloudflare.com`. Turnstile works
whether or not the rest of the service runs behind Cloudflare.

## The web app on a Cloudflare Worker

The web app's static files can be served by a Cloudflare Worker from Cloudflare's edge, with `/api/` forwarded to the
portal API on your servers, so the app and its API stay on one origin (the sign-in cookie needs that).

> [!NOTE]
> Tested locally with `wrangler dev` against a stand-in API; not yet deployed to production. The self-contained
> setup (the web app served from your servers) is the default.

1. In `deploy/k8s/deploy.env`: `FRONTEND=worker`, `API_ORIGIN_HOST` (e.g. `pdf-origin.example.org`),
   `API_ORIGIN_ALLOWED_SOURCES` (Cloudflare's published ranges, https://www.cloudflare.com/ips/: the only sources
   the API origin answers) and `API_ORIGIN_SOURCE_CIDR` (where the ingress controller's traffic comes from; the node
   network for one that runs on the nodes, as RKE2's does). `deploy.sh` refuses to go on without them.
2. Route `API_ORIGIN_HOST` through the tunnel (public hostname → `portal-api.pdf-remediation.svc.cluster.local:8080`)
   and protect it with **Cloudflare Access**: an application for that host with a service-token policy. Put the token
   in `deploy/k8s/worker.env` as `ACCESS_CLIENT_ID=…` and `ACCESS_CLIENT_SECRET=…`.
3. `scripts/deploy.sh`: the cluster side (an Ingress for the API origin instead of the web pod; the portal reads the
   caller's address from `x-client-ip`, which the Worker always sets).
4. `scripts/deploy-worker.sh`: builds the web app and publishes the Worker on the app's hostname (sign in with
   `CLOUDFLARE_API_TOKEN`, or `npx wrangler login` once). `--dry-run` checks without publishing. Remove the tunnel's
   public hostname for the app first: the Worker's custom domain takes its place.

> **Changed 23 October 2026:** `/build.json` is kept uncached (#575).

With the Worker there's no web pod, so no Grafana; Prometheus, its alerts and Administration › System are the
same. The Worker is `deploy/cloudflare-worker/worker.js`; security headers come from `web/public/_headers`, which
also keeps `/build.json` (the build's id, which pages open before a deploy compare on the next click, #575) from being
cached. To go
back, set `FRONTEND=cluster`, rerun `scripts/deploy.sh`, delete the Worker, and route the hostname through the
tunnel again.

## Running more of it on Cloudflare

> [!WARNING]
> A design only: not built or tested. Cloudflare's products and limits change; check their current documentation.

What maps well:

| Part | On Cloudflare | Notes |
|---|---|---|
| Web app | A Worker with static assets (above) | Ready |
| Stored files | **R2** (S3-compatible) | Set `PORTAL_S3_ENDPOINT_URL` to the account's R2 endpoint, with an R2 API token's keys, and drop SeaweedFS |
| Email | **Cloudflare Email Service** over SMTP | Ready ([settings](../operations/email-and-branding.md#email)) |
| Edge protection | WAF, rate limiting, **Turnstile** on the sign-in page ([above](#a-bot-check-on-sign-in)), Access for admin areas | Configured in Cloudflare |

What doesn't map directly:

| Part | The difficulty | A way through |
|---|---|---|
| Portal API, portal worker, engine | Long-running Python processes; the engine needs Java, Tesseract and a few GB of memory per document, and jobs can run for minutes | **Cloudflare Containers** can run the existing images, within its instance sizes and limits. Otherwise, keep them on a server or cluster behind a tunnel (the tested setup) |
| The engine's shared job folder | Containers have no shared filesystem | Run the engine API and a worker in **one** container instance, or add an object-storage job store to the engine (`app/storage.py`) |
| PostgreSQL | No managed PostgreSQL on Cloudflare (D1 is SQLite, and the portal uses PostgreSQL features) | A managed PostgreSQL elsewhere, reached through **Hyperdrive** or directly |
| Redis | None on Cloudflare | A managed Redis elsewhere (for example Upstash) |
| AI models | Workers AI serves models, but not necessarily the vision models used here | Keep the AI gateway, pointing at your model server or a cloud provider; Workers AI could be added as a gateway model where a suitable vision model exists |

So the most that runs **entirely** on Cloudflare today is the edge, the web app, file storage and email; the
application containers, PostgreSQL and Redis need a home of their own, or Cloudflare Containers plus outside database
services.
