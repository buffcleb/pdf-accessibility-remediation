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
| `verapdf` (PDF/UA checks, kept running), `verapdf-restart` (restarts it every hour) | Deployment + Service, CronJob | – |
| `engine-worker` scaling, `verapdf` and `litellm` autoscalers (with [scaling on](#scaling-with-the-queue)) | KEDA ScaledObject, HorizontalPodAutoscalers | – |
| `music-reader` (optional: `MUSIC_READER=on`), `formula-reader` (optional: `FORMULA_READER=on`) | Deployments, KEDA ScaledObjects with scaling on | – |
| `prometheus`, `grafana`, `kube-state-metrics`, Redis and Postgres exporters (with `MONITORING=on`) | Deployments | `MONITORING_STORAGE_CLASS` |
| `backup` (optional: `BACKUPS=cronjob`) | CronJob | an NFS share |
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
| `env2secret.py` | yes | Turns env files into a Kubernetes Secret, all keys or a chosen set (quotes and PEM keys handled) |
| `app-role.sql`, `monitoring-role.sql` | yes | The portal's own database account (`portal_app`, rows only) and the Postgres exporter's (reads no table) |
| `monitoring/` | yes | Prometheus's config and alert rules (with their tests) and Grafana's dashboards |
| `deploy.env` | **no** | Your deployment settings (made by `deploy.sh init`) |
| `engine.env`, `portal.env` | **no** | Secrets for the engine and gateway, and for the portal (made by `deploy.sh init`, mode 600) |
| `tunnel.env` | **no** | `TUNNEL_TOKEN=…` for the optional Cloudflare tunnel |
| `worker.env` | **no** | `ACCESS_CLIENT_ID` and `ACCESS_CLIENT_SECRET` for the optional Cloudflare Worker |
| `.last-build` | **no** | The image tags `build.sh` last pushed |

### Which pod sees which secret

The two env files are split into five secrets (six with monitoring), so a pod holds what it reads and nothing more:

| Secret | From | Holds | Read by |
|---|---|---|---|
| `pdfrem-engine-env` | `engine.env` | Everything but the gateway's keys (`LITELLM_*`, `UI_*`, `LOCAL_*`, `OPENROUTER_*`) and `ADOBE_CLIENT_*` | The engine's API, workers and scheduler; Redis, the music and formula readers and KEDA (`REDIS_PASSWORD`) |
| `pdfrem-gateway-env` | `engine.env` | Everything but the engine's own (`REDIS_PASSWORD`, `SERVICE_API_KEY`, `GATEWAY_API_KEY`, `LOCALAI_*`, `OLLAMA_*`, `ADOBE_*`) | The AI gateway |
| `pdfrem-adobe-env` | `engine.env` | `ADOBE_CLIENT_ID`, `ADOBE_CLIENT_SECRET` | The Adobe workers only |
| `pdfrem-db-env` | both | `POSTGRES_ADMIN_PASSWORD`, `PORTAL_DB_PASSWORD`, `LITELLM_DB_PASSWORD` | Postgres; the migration job |
| `pdfrem-portal-env` | `portal.env` | Everything but `POSTGRES_ADMIN_PASSWORD`, `PORTAL_DB_PASSWORD` and monitoring's three | The portal's API and worker; SeaweedFS (the S3 keys) and KEDA (`PORTAL_APP_DB_PASSWORD`) |
| `pdfrem-monitoring-env` | `portal.env` | `MONITORING_DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_SECRET_KEY` (with `MONITORING=on`) | The Postgres exporter; Grafana |

An engine worker, the part that opens untrusted documents, no longer sees the gateway's master key, the
OpenRouter key or the Adobe secret. (Its `GATEWAY_API_KEY` is the master key as `init` makes it; a key with
less reach, made in the gateway's UI, can be put in its place.) The engine's own `OPENROUTER_API_KEY` setting
is therefore empty on a cluster: cloud models go through the gateway, as the AI levels do.

### `deploy.env` at a glance

`deploy.env.example` says what each setting does. Grouped, with where the longer story is:

| Settings | What they are |
|---|---|
| `KUBECONFIG_PATH`, `KUBE_CONTEXT`, `NAMESPACE` | Which cluster, context and namespace (`pdf-remediation`) |
| `APP_HOST`, `FRONTEND`, `API_ORIGIN_HOST`, `API_ORIGIN_SOURCE_CIDR`, `API_ORIGIN_ALLOWED_SOURCES`, `WORKER_NAME` | The public address, and the [Worker front end](#optional-the-web-app-on-a-cloudflare-worker) |
| `TRUSTED_PROXY_CIDRS`, `WEB_TRUSTED_PROXIES` | Who may name the caller ([below](#network-policies-and-the-redis-password)) |
| `REGISTRY`, `ENGINE_TAG`, `PORTAL_TAG`, `WEB_TAG` (and `MUSIC_TAG`, `FORMULA_TAG`) | Where images come from; tags are taken from `.last-build` unless set |
| `LITELLM_IMAGE`, `VERAPDF_IMAGE`, `CURL_IMAGE`, `CLOUDFLARED_IMAGE` | Outside images, pinned by digest: moving one is a choice |
| `GATEWAY_ADMIN_URL`, `GATEWAY_SHARED_LOGIN` | The AI gateway's admin UI ([AI models](../../docs/operations/ai-models.md)) |
| `STORAGE_CLASS`, `SHARED_STORAGE_CLASS`, `DB_SIZE`, `REDIS_SIZE`, `FILES_SIZE`, `JOBDATA_SIZE` | Storage (required) and volume sizes (10, 1, 50 and 50 Gi) |
| `ENGINE_WORKERS`, `WORKER_CONCURRENCY`, `WORKER_MEMORY_LIMIT`, `ADOBE_WORKERS` | Engine capacity ([sizing](#sizing)); Adobe workers stay at 0 until [Adobe](../../docs/operations/adobe-autotag.md) is set up |
| `VERAPDF_HEAP`, `VERAPDF_MEMORY_LIMIT`, `VERAPDF_RESTART_SCHEDULE` | The [veraPDF service](../../docs/operations/configuration.md#verapdf-service) |
| `AUTOSCALE`, `ENGINE_WORKERS_MAX`, `VERAPDF_MAX`, `LITELLM_MAX`, `SCALE_DOWN_SECONDS` | [Scaling with the queue](#scaling-with-the-queue) |
| `MUSIC_*`, `FORMULA_*` | The optional [music](#music-notation-read-note-by-note) and [formula](#formulas-read-a-second-time-from-their-pictures) readers |
| `MAX_UPLOAD_SIZE` | Largest upload through the web app and Ingress (`100m`) |
| `BACKUPS`, `BACKUP_*` | [Nightly backups](../../docs/operations/backups-and-migrations.md#nightly-backups-on-the-cluster) |
| `MONITORING`, `PROMETHEUS_*`, `GRAFANA_SIZE`, `MONITORING_STORAGE_CLASS` | [Prometheus and Grafana](../../docs/operations/monitoring.md#prometheus-and-grafana) |

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
others keep their tags). Each pushed image is scanned with Trivy (#362): HIGH and CRITICAL vulnerabilities that
have a fix are listed, and an image with a CRITICAL one is **held**, pushed but not recorded, so `deploy.sh` keeps
deploying the last one (`build.sh` exits 3 and says which). Rebuild once the base image or package has the fix (a
Dependabot pull request usually brings it), or record it anyway with `--accept-findings`; a finding judged not to
apply goes in `.trivyignore` with the reason. `--no-scan` skips the scan.

`build.sh` builds the engine, portal and web images, plus `music` with `MUSIC_READER=on` and `formula` with
`FORMULA_READER=on` in `deploy.env`. Its other options: `--tag TAG`, `--no-push` (a build check),
`--platform` (`linux/amd64`), `--allow-dirty` (uncommitted changes, tagged `-dirty`). The engine image includes
MathCAT (formula speech and braille, #403), compiled from `docker/mathcat` in a Rust build stage, so a build with
no cache takes a few minutes longer; there's nothing to set for it.

`deploy.sh` asks before changing the cluster (`-y` skips the question), makes the namespace and secrets, starts
PostgreSQL, Redis and SeaweedFS, **backs up the databases into `./backups` if they already exist** (`--skip-backup`
doesn't, and isn't recommended), runs the migrations as a Job (stopping if they fail), sets up the portal's own
database account (below), rolls out the gateway, engine, portal, web app and the optional pieces that are on, and
checks each one answers.

**The portal's database account** (#337). Migrations run as the database's owner (`portal`, `PORTAL_DB_PASSWORD`);
the portal's pods and KEDA use `portal_app` (`PORTAL_APP_DB_PASSWORD`, made in `portal.env` by `deploy.sh`), which
reads and writes rows but owns no table, so it can't alter a table or drop the triggers that keep the audit log and
the page ledger append-only. `app-role.sql` makes it and grants it the tables on every deploy, after the
migrations; after restoring a database, run `deploy.sh` once so the restored tables are granted again.

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
`sign-in-code.sh` prints a sign-in code when email fails (audited, never the code). The `kubectl` commands for
looking at pods, logs, events, autoscalers and policies are listed with what each shows in
[Monitoring → On the cluster](../../docs/operations/monitoring.md#on-the-cluster).

## Network policies and the Redis password

Everything inside the namespace is closed to pods that have no business with it (`manifests/80-network-policies.yaml`):
Postgres takes connections from the portal, the gateway, the migration job, the nightly backup and KEDA; Redis
from the engine, the portal, the music and formula readers and KEDA; SeaweedFS from the portal and the nightly
backup, on its S3 port only (its master, filer and volume ports check nothing); veraPDF from the engine; the AI
gateway from the engine and the portal; the engine's API from the portal; the portal's API from the web pod and
cloudflared. The workers, the scheduler and the two readers answer no one but Prometheus. The web pod and
cloudflared keep the cluster's default (open), as the way in.

With monitoring on (`MONITORING=on`, `manifests/95-monitoring.yaml`), Prometheus is also let in to the portal's,
the engine's and the workers' metrics ports, its exporters to Postgres and Redis, the portal and Grafana to
Prometheus, and only the web pod to Grafana ([monitoring](../../docs/operations/monitoring.md#prometheus-and-grafana)).

Egress is closed for the pods that open uploaded PDFs: the engine's API, workers and scheduler reach only DNS,
Redis, veraPDF and the AI gateway, and the music and formula readers only DNS and Redis, so an engine taken over
by a crafted PDF can't send anything out (#361). Their AI calls all go through the gateway (`AI_GATEWAY_ONLY=true` in
`40-engine.yaml`: a provider named directly, such as the portal's fallback or the default judge, becomes the
gateway's default or judge model, `GATEWAY_DEFAULT_MODEL` and `GATEWAY_JUDGE_MODEL` in engine.env), and the Jev
classifier goes through the gateway's `/systemone` route, which adds the OpenRouter key. The gateway, the Adobe
worker (Adobe's API) and the portal keep their internet access.
The cluster's network plugin must enforce `NetworkPolicy` (RKE2's Canal does; `kubectl get networkpolicy -n
<namespace>` lists what is applied).

Redis also asks for a password now: `REDIS_PASSWORD` in `engine.env` and the same value as `PORTAL_REDIS_PASSWORD`
in `portal.env`, which `deploy.sh` makes and keeps together (it also keeps `SERVICE_API_KEY` and
`PORTAL_ENGINE_API_KEY` together, so the engine's API always has its key). A deployment made before these existed
gets them on its next `deploy.sh`: the keys are added to the env files, Redis restarts with the password, and the
pods that use it roll with their new address; between the two there can be a minute of queue errors in the logs.

**With the Worker front end** (`FRONTEND=worker`) the portal API is also let in from the ingress controller
(`81-api-origin-policy.yaml`). RKE2's ingress controller runs in the nodes' own network, where its traffic is seen by
the node's address: set `API_ORIGIN_SOURCE_CIDR` in `deploy.env` to the node network, or the API origin answers no
one. An ingress controller that runs as a pod in `kube-system` needs nothing more.

**Who may name the caller** (#336). The portal reads the caller's address from a header only on a connection from
`TRUSTED_PROXY_CIDRS` (the pod range, `10.42.0.0/16` by default on RKE2: cloudflared and the web pod), plus
`API_ORIGIN_SOURCE_CIDR` with the Worker front end; from anything else it uses the connection's own address. The web
pod passes Cloudflare's header (`CF-Connecting-IP`) on only from that range too, so someone reaching it through the
web Ingress on a node's address can't make one up. With `FRONTEND=worker`, `deploy.sh` also requires
`API_ORIGIN_ALLOWED_SOURCES` (Cloudflare's published ranges, https://www.cloudflare.com/ips/): the API origin's
Ingress answers only them. An ingress controller that runs as a pod inside the pod range would be trusted as well;
RKE2's runs on the nodes' network, so it isn't. Putting Cloudflare Access in front of the API origin as well is still
recommended.

**On first applying this:** watch that the readiness probes keep passing (`scripts/deploy.sh status`); the probes
come from each node, which Canal lets through, but a different network plugin might not, and then every probed pod
would need a rule for the node network as well.

## What a pod may do and use

Every pod runs under the runtime's default seccomp profile, and every container drops all capabilities and
may not gain privilege. The engine's four containers, Redis, the web pod, cloudflared and the portal run on a
read-only root filesystem: the engine writes only to `/tmp` and the job folder, and the three places that
start as root keep only what that step needs (Postgres, to make the data directory its own user's before it
drops to it; the gateway's image, which has no other user; SeaweedFS now runs as its own `seaweed` user, with
`fsGroup` giving it the data volume).

Scratch space is bounded too: each `/tmp` is an emptyDir with a `sizeLimit` (4 Gi for an engine worker and for
veraPDF, 2 Gi for the portal's API and the music reader, less elsewhere) and each container has an
`ephemeral-storage` limit a little above it. A document that spills more than that gets its pod **evicted**
rather than filling the node: `kubectl -n <namespace> get events --field-selector reason=Evicted` shows it,
the job fails with the worker, and the limit (`40-engine.yaml`, `35-verapdf.yaml`) is the thing to raise if
it was a legitimate document. Redis is capped at 384 MB with `noeviction`: when the queues are full a new
request is refused (the portal reports the error) instead of Redis quietly dropping keys or being killed;
`kubectl -n <namespace> exec sts/redis -- redis-cli -a "$REDIS_PASSWORD" info memory` shows how much it holds.

**On first applying this:** SeaweedFS restarts as uid 1000, and the kubelet makes its data volume that
group's on the way up (once; it checks only the top directory after that). Postgres keeps its data directory
as it is. If a pod stays in `CreateContainerConfigError` or `Error`, `kubectl -n <namespace> describe pod` and
its logs say which setting it couldn't live with; `readOnlyRootFilesystem: true` on that container is the one
to take off first.

## Sizing

Memory requests add up to about 3.9 GiB (4.6 GiB with monitoring on), including 768 MiB for the veraPDF service
(`VERAPDF_MEMORY_LIMIT`, 1 GiB); each engine worker may use up to `WORKER_MEMORY_LIMIT` (4 GiB) and 3 CPUs on large
documents. Worker pods × `WORKER_CONCURRENCY`
(documents per pod) is how many documents are processed at once. The portal hands the engine as many documents as
its running workers have places for (at least `PORTAL_ENGINE_MAX_IN_FLIGHT`, 4).

### Scaling with the queue

With [KEDA](https://keda.sh/docs/latest/deploy/) and metrics-server on the cluster, `deploy.sh` turns scaling on
(`AUTOSCALE=auto`; `on` insists, `off` keeps fixed sizes). It says which at the start of a deploy.

| What | Follows | Between |
|---|---|---|
| Engine workers | Documents waiting or in progress in the portal: one pod per `WORKER_CONCURRENCY` | `ENGINE_WORKERS` and `ENGINE_WORKERS_MAX` (1 and 4) |
| veraPDF | Its CPU use (more than one CPU busy per copy) | 1 and `VERAPDF_MAX` (3) |
| AI gateway | Its CPU use | 1 and `LITELLM_MAX` (2) |

- **Going up** is quick (up to 2 worker pods a minute). **Going down** waits for `SCALE_DOWN_SECONDS` (600) of
  quiet, one pod at a time, and a worker told to stop finishes its document first (up to `WORKER_GRACE_SECONDS`).
- **Documents that more workers wouldn't start don't count:** those held by a paused queue, a daily page limit or
  a service's **Most documents at once**.
- **Size the maximum to the nodes.** Each document keeps about one CPU busy, and a worker pod may use
  `WORKER_MEMORY_LIMIT`. Three nodes of 4 CPUs and 16 GiB can process about 8 documents at once (4 pods of 2)
  beside everything else. Past that, CPU is the limit: add CPUs or a node, then raise `ENGINE_WORKERS_MAX`.
- **AI that can't grow:** a local AI's GPUs serve only so many documents. Set that AI level's **Most documents
  at once** (Administration › Services › Pages and cost guards) to what they handle; the rest wait in the queue.
  Cloud and commercial levels scale at the provider; the gateway's `max_budget` (`config/litellm.yaml`) and each
  level's page limits bound the cost.
- KEDA reads the count as the portal's rows-only account, `portal_app` (`PORTAL_APP_DB_PASSWORD` in the
  `pdfrem-portal-env` secret).
- `WORKER_GRACE_SECONDS` (3900, a little over `TASK_TIME_LIMIT_S`; not in `deploy.env.example`) is how long a
  stopping worker pod may take to finish.
- `kubectl -n <namespace> get scaledobject,hpa` shows the current and wanted numbers.

### Music notation read note by note

Optional (`MUSIC_READER=on` in `deploy.env`). The engine finds music examples either way and tags each as one
figure; with this on, its alt text is a reading of the notes by [Audiveris](https://github.com/Audiveris/audiveris)
instead of an AI description of the picture.

How much music is read and how long the engine waits are in `deploy.env` too (`MUSIC_MAX_EXAMPLES`,
`MUSIC_MAX_SCORE_LINES`, `MUSIC_PER_MEASURE`, `MUSIC_WHOLE_SCORE`, `MUSIC_WHOLE_SCORE_MAX_MB`,
`MUSIC_WAIT_SECONDS`, `MUSIC_PAGE_SECONDS`); `deploy.env.example` says what each one does. They are set on
the engine's workers by the manifests, and take the place of anything of the same kind in `engine.env`.

Rebuild the image when `docker/audiveris` changes (reading a whole score as one piece needs the service from
this version on; an older one still reads examples, and sheet music then has no whole-piece file).

1. `scripts/build.sh --only music` builds and pushes `pdfrem-music` (about 480 MB; it downloads Audiveris and a
   Tesseract language file, so it only needs rebuilding for a new Audiveris version).
2. Set `MUSIC_READER=on` and run `scripts/deploy.sh`.

With scaling on, no pod runs until a document has music: KEDA starts one when a picture is put on the Redis list
`omr:requests`, adds another per `MUSIC_PER_POD` (4) waiting up to `MUSIC_MAX` (2), and removes them after
`SCALE_DOWN_SECONDS` of quiet. The first reading waits for the pod to start (longer the first time a node pulls
the image); if nothing answers within `MUSIC_WAIT_SECONDS` (240) the examples are described
by the AI as before. Without scaling, one pod is always running (768 MiB requested, up to `MUSIC_MEMORY_LIMIT`).

### Formulas read a second time, from their pictures

Optional (`FORMULA_READER=on` in `deploy.env`, #403). Each formula the AI read is also read from its picture by a
formula reader, [RapidLaTeXOCR](https://github.com/RapidAI/RapidLaTeXOCR) (Apache-2.0; pix2tex's model, MIT, run with
ONNX Runtime, about a second a formula on a CPU), independent of the AI. The two are compared by rule (MathCAT reads
both): where they say other numbers or letters, the formula is flagged and Edit tag offers every reading. The
reader is noisier than the AI, so its equation numbers and padding are taken off, and a reading that shows it
couldn't read the picture, or says far more or less than the AI's, is set aside rather than flagged. On a real
textbook chapter (Local AI) it compared 39 of 65 formulas and flagged 4.

1. `scripts/build.sh --only formula` builds and pushes `pdfrem-formula` (about 870 MB; the model is downloaded
   while building and checked against its SHA-256).
2. Set `FORMULA_READER=on` and run `scripts/deploy.sh`.

With scaling on, no pod runs until a document has formulas: KEDA starts one when a picture is put on the Redis
list `formula:requests`, adds another per `FORMULA_PER_POD` (20) waiting up to `FORMULA_MAX` (2), and removes them
after `SCALE_DOWN_SECONDS` of quiet. If nothing answers within three minutes the formulas are checked as before.
The reader reaches only Redis and DNS, and only Prometheus reaches it.
