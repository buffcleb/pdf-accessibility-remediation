# Kubernetes manifests and scripts

The reference for `deploy/k8s`: every setting, the optional pieces and day-to-day commands. For the walkthrough, see
[Deployment → Kubernetes](../../docs/deployment/kubernetes.md); for a worked example on a real cluster, the
[reference RKE2 deployment](../../docs/deployment/reference-rke2.md).

Two scripts do the work:

```bash
scripts/build.sh      # build the images (linux/amd64) and push them to your registry
scripts/deploy.sh     # back up, migrate and roll out on the cluster
```

Everything runs in one namespace (`pdf-remediation` by default), reachable at one address (`APP_HOST`): through an
Ingress (class `nginx`) or the optional Cloudflare tunnel, to the web app, which proxies `/api/` to the portal API.

| Piece | Kind | Storage |
|---|---|---|
| `web` | Deployment + Service + Ingress | – |
| `portal-api`, `portal-worker` | Deployments | – |
| `engine-api`, `engine-worker`, `engine-beat`, `engine-adobe-worker` (0 replicas until Adobe is set up) | Deployments | the shared job folder (`SHARED_STORAGE_CLASS`, ReadWriteMany) |
| `litellm` (the AI gateway; its admin UI isn't exposed) | Deployment | its database |
| `postgres` (portal and gateway databases) | StatefulSet | `STORAGE_CLASS` |
| `redis` (engine queue on db 0, portal on db 1) | StatefulSet | `STORAGE_CLASS` |
| `seaweedfs` (S3 for stored files) | StatefulSet | `STORAGE_CLASS` |
| `cloudflared` (optional: with `deploy/k8s/tunnel.env`) | Deployment, 2 replicas | – |
| `api-origin` (optional: with `FRONTEND=worker`) | Ingress for the portal API | – |

## Files

| File | In git | What it is |
|---|---|---|
| `manifests/*.yaml`, `migrate-job.yaml` | yes | The manifests, with `${SETTINGS}` filled in by `deploy.sh` |
| `deploy.env.example` | yes | Every deployment setting, with comments |
| `env2secret.py` | yes | Turns an env file into a Kubernetes Secret (quotes and PEM keys handled) |
| `deploy.env` | **no** | Your deployment settings (made by `deploy.sh init`) |
| `engine.env`, `portal.env` | **no** | Secrets for the engine and gateway, and for the portal (made by `deploy.sh init`, mode 600) |
| `tunnel.env` | **no** | `TUNNEL_TOKEN=…` for the optional Cloudflare tunnel |
| `worker.env` | **no** | `ACCESS_CLIENT_ID` and `ACCESS_CLIENT_SECRET` for the optional Cloudflare Worker |
| `.last-build` | **no** | The image tags `build.sh` last pushed |

## Before the first deploy (once)

1. **Storage classes:** a block class (`STORAGE_CLASS`) and a ReadWriteMany class (`SHARED_STORAGE_CLASS`). Check with
   `kubectl get storageclass`. The engine runs as UID 10001 and must be able to write to the shared volume; an init
   container checks this and says so if it can't.
2. **Registry access.** For GHCR, `gh auth refresh -s write:packages` (the build pushes with your `gh` token; the
   cluster pulls with a secret made from it, or from `GHCR_PULL_TOKEN`, a read-only token, if set). For another
   registry, set `GHCR_TOKEN` and `GHCR_PULL_TOKEN` to its credentials.
3. **A way in:** an Ingress controller with DNS and TLS for `APP_HOST`, or the Cloudflare tunnel: in Cloudflare Zero
   Trust → Networks → Tunnels, create a tunnel, put its token in `deploy/k8s/tunnel.env` as `TUNNEL_TOKEN=…`, and add a
   public hostname `APP_HOST` → HTTP → `web.<namespace>.svc.cluster.local:8080`. Uploads through Cloudflare are capped
   by its plan (100 MB on Free and Pro), which `MAX_UPLOAD_SIZE` should match.
4. **Settings and secrets:** `scripts/deploy.sh init` makes `deploy.env`, `engine.env` and `portal.env`. The two env
   files are copied from your local `.env` and `.env.portal` if present, with **new** passwords and keys. Review all
   three, and **set the mail settings** in `portal.env` (Mailpit isn't deployed).

The cluster starts empty. To carry over a local test stack's documents and accounts, back up locally
(`scripts/backup.sh --all`) and restore into the cluster (`scripts/restore.sh --k8s --from … --db --files`).

## Each release

```bash
git pull
```

```bash
scripts/build.sh
```

```bash
scripts/deploy.sh
```

`build.sh` tags the images with the commit and records them in `.last-build` (`--only web` rebuilds one image; the
others keep their tags). `deploy.sh` asks before changing the cluster (`-y` skips the question), makes the namespace
and secrets, starts PostgreSQL, Redis and SeaweedFS, **backs up the databases into `./backups` if they already
exist**, runs the migrations as a Job (stopping if they fail), rolls out the gateway, engine, portal and web app, and
checks each one answers.

Changing `engine.env`, `portal.env` or `tunnel.env` and rerunning `deploy.sh` restarts the pods that use them (their
templates carry the file's checksum). `scripts/deploy.sh render` writes the manifests as they'd be applied to
`deploy/k8s/.rendered/`, without touching the cluster.

## Optional: the web app on a Cloudflare Worker

By default the cluster serves everything. Instead, a Cloudflare Worker can serve the web app from Cloudflare's edge
and forward `/api/` to the portal API on the cluster; see
[Deployment → Cloudflare](../../docs/deployment/cloudflare.md#the-web-app-on-a-cloudflare-worker):
`FRONTEND=worker` and `API_ORIGIN_HOST` in `deploy.env`, the Access token in `worker.env`, then `scripts/deploy.sh` and
`scripts/deploy-worker.sh`.

## Day to day

```bash
scripts/deploy.sh status
```

```bash
scripts/backup.sh --k8s
```

```bash
scripts/restore.sh --k8s --from backups/backup-… --db
```

```bash
kubectl -n pdf-remediation port-forward svc/litellm 4000
```

```bash
kubectl -n pdf-remediation logs deploy/engine-worker -f
```

```bash
scripts/sign-in-code.sh person@example.org --k8s --reason "mail outage"
```

The gateway's admin UI is then at http://localhost:4000/ui. `backup.sh --k8s --all` adds stored files and config.
`sign-in-code.sh` prints a sign-in code when email fails (audited, never the code).

## Sizing

Requests add up to about 3.5 GiB; each engine worker may use up to `WORKER_MEMORY_LIMIT` (4 GiB) on large documents.
`ENGINE_WORKERS` (pods) × `WORKER_CONCURRENCY` (documents per pod) is how many documents are processed at once; raise
the portal's `PORTAL_ENGINE_MAX_IN_FLIGHT` (4 by default) to match.
