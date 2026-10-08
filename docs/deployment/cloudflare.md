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

## The web app on a Cloudflare Worker

The web app's static files can be served by a Cloudflare Worker from Cloudflare's edge, with `/api/` forwarded to the
portal API on your servers, so the app and its API stay on one origin (the sign-in cookie needs that).

> [!NOTE]
> Tested locally with `wrangler dev` against a stand-in API; not yet deployed to production. The self-contained
> setup (the web app served from your servers) is the default.

1. In `deploy/k8s/deploy.env`: `FRONTEND=worker`, and `API_ORIGIN_HOST` (e.g. `pdf-origin.example.org`).
2. Route `API_ORIGIN_HOST` through the tunnel (public hostname → `portal-api.pdf-remediation.svc.cluster.local:8080`)
   and protect it with **Cloudflare Access**: an application for that host with a service-token policy. Put the token
   in `deploy/k8s/worker.env` as `ACCESS_CLIENT_ID=…` and `ACCESS_CLIENT_SECRET=…`.
3. `scripts/deploy.sh`: the cluster side (an Ingress for the API origin instead of the web pod; the portal reads the
   caller's address from `x-client-ip`, which the Worker always sets).
4. `scripts/deploy-worker.sh`: builds the web app and publishes the Worker on the app's hostname (sign in with
   `CLOUDFLARE_API_TOKEN`, or `npx wrangler login` once). `--dry-run` checks without publishing. Remove the tunnel's
   public hostname for the app first: the Worker's custom domain takes its place.

The Worker is `deploy/cloudflare-worker/worker.js`; security headers come from `web/public/_headers`. To go back, set
`FRONTEND=cluster`, rerun `scripts/deploy.sh`, delete the Worker, and route the hostname through the tunnel again.

## Running more of it on Cloudflare

> [!WARNING]
> A design only: not built or tested. Cloudflare's products and limits change; check their current documentation.

What maps well:

| Part | On Cloudflare | Notes |
|---|---|---|
| Web app | A Worker with static assets (above) | Ready |
| Stored files | **R2** (S3-compatible) | Set `PORTAL_S3_ENDPOINT_URL` to the account's R2 endpoint, with an R2 API token's keys, and drop SeaweedFS |
| Email | **Cloudflare Email Service** over SMTP | Ready ([settings](../operations/email-and-branding.md#email)) |
| Edge protection | WAF, rate limiting, **Turnstile** on the sign-in page (planned), Access for admin areas | Configured in Cloudflare |

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
