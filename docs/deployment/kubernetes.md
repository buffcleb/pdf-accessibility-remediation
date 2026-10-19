# Kubernetes

[Deployment](README.md) › Kubernetes

`deploy/k8s` has plain manifests (no Helm chart) and two scripts that build, deploy, back up and migrate. They work on
any conformant cluster that provides the storage below. The [reference deployment](reference-rke2.md) is one such
cluster, with every step filled in.

> [!NOTE]
> Tested on the reference cluster (RKE2 1.33). Other distributions (k3s, EKS, AKS, GKE, OpenShift) should work with
> the storage classes and ingress changed, but haven't been tried.

## What it creates

One namespace (`pdf-remediation` by default):

| Workload | Kind | Storage |
|---|---|---|
| `web` | Deployment + Service + Ingress | – |
| `portal-api`, `portal-worker` | Deployments | – |
| `engine-api`, `engine-worker`, `engine-beat`, `engine-adobe-worker` (0 replicas until Adobe is set up) | Deployments | The shared job folder |
| `litellm` (the AI gateway; its admin UI isn't exposed) | Deployment | – |
| `verapdf` (PDF/UA checks, kept running), `verapdf-restart` (hourly) | Deployment, CronJob | – |
| `postgres` (portal and gateway databases) | StatefulSet | Block (ReadWriteOnce) |
| `redis` (engine queue on db 0, portal on db 1) | StatefulSet | Block |
| `seaweedfs` (S3 for stored files) | StatefulSet | Block |
| `cloudflared` (optional, with a tunnel token) | Deployment, 2 replicas | – |
| `music-reader`, `formula-reader` (optional: `MUSIC_READER=on`, `FORMULA_READER=on`) | Deployments | – |
| `prometheus`, `grafana` and three exporters (`MONITORING=on`, the default) | Deployments | Block |
| `backup` (optional: `BACKUPS=cronjob`) | CronJob | An NFS share |
| `portal-migrate-<time>` | Job, per deploy | – |

Every pod runs without added privileges and drops all capabilities; most run as non-root on a read-only root
filesystem. Network policies close each service to pods that have no business with it, and the pods that open
uploaded PDFs reach nothing outside the cluster but through the AI gateway
([details](../../deploy/k8s/README.md#network-policies-and-the-redis-password)).

## What the cluster needs

- **Two storage classes:**
  - a **block** class (ReadWriteOnce) for PostgreSQL, Redis and SeaweedFS (`STORAGE_CLASS`): iSCSI, Ceph RBD,
    Longhorn, a cloud disk;
  - a **shared** class (ReadWriteMany) for the engine's job folder (`SHARED_STORAGE_CLASS`): NFS (for example with
    nfs-subdir-external-provisioner or csi-driver-nfs), CephFS, Longhorn RWX, EFS, Azure Files. The engine runs as
    UID 10001, so the share must let it write; an init container checks and says so if it can't.
- **An image registry** the cluster can pull from (`scripts/build.sh` pushes to GHCR by default; any registry works).
- **A way in:** an ingress controller (the manifests use `ingressClassName: nginx`), or the bundled Cloudflare Tunnel.
- **amd64 nodes** (the images are built for linux/amd64), with about 6–8 GB of memory free in total for a small
  deployment.
- **A network plugin that enforces `NetworkPolicy`** (RKE2's Canal does, as do Calico and Cilium).
- **Optional: [KEDA](https://keda.sh) and metrics-server**, for scaling with the queue. Without them the sizes stay
  fixed.

## Steps

1. **Settings.** `scripts/deploy.sh init` makes three git-ignored files (mode 600): `deploy/k8s/deploy.env` (cluster,
   namespace, hostname, registry, storage classes, sizes), and `engine.env` and `portal.env` (secrets, copied from your
   local `.env` and `.env.portal` if present, with **new** passwords and keys). Review all three; set the mail settings
   in `portal.env`.
2. **Registry access.** For GHCR: `gh auth refresh -s write:packages`, or set `GHCR_TOKEN` (push) and
   `GHCR_PULL_TOKEN` (pull).
3. **Build and push** the images (engine, portal and web; the music and formula readers too when they're on),
   tagged with the commit. Each is scanned with Trivy:

   ```bash
   scripts/build.sh
   ```

4. **Deploy:**

   ```bash
   scripts/deploy.sh
   ```

   It shows the cluster, namespace and image tags and asks before changing anything. Then it creates the namespace and
   secrets; starts PostgreSQL, Redis and SeaweedFS; **backs up the databases** if they already exist; runs the
   migrations as a Job (stopping if they fail); sets up the portal's own database account (`portal_app`, rows only);
   rolls out the gateway, veraPDF, engine, portal, web app and the optional pieces that are on; and checks each one
   answers.

5. **A way in:** point your ingress controller's DNS at the hostname in `deploy.env` (`APP_HOST`), with TLS from your
   ingress setup (for example cert-manager), or use the [Cloudflare Tunnel](cloudflare.md#a-tunnel-in-front-tested).

`deploy/k8s/README.md` has the full reference: every setting, the optional pieces and day-to-day commands.

## Day to day

```bash
scripts/deploy.sh status
```

```bash
scripts/backup.sh --k8s
```

```bash
kubectl -n pdf-remediation port-forward svc/litellm 4000
```

- Editing `engine.env`, `portal.env` or `tunnel.env` and rerunning `scripts/deploy.sh` restarts the pods that use them.
- `scripts/build.sh --only web` rebuilds one image; the others keep their last tags. Each image is scanned with
  Trivy after the push, and one with a CRITICAL vulnerability that has a fix isn't deployed until it's rebuilt or
  accepted (`--accept-findings`; [details](../../deploy/k8s/README.md)).
- `scripts/deploy.sh render` writes the manifests as they'd be applied, without touching the cluster.
- `scripts/sign-in-code.sh person@example.org --k8s` issues a sign-in code when email fails.

## Scaling

Worker pods × `WORKER_CONCURRENCY` (documents per pod, 2) is how many documents are processed at once. The portal
asks the engine how many places its running workers have, so more workers are used without changing the portal.
With KEDA and metrics-server on the cluster, workers follow the queue between `ENGINE_WORKERS` and
`ENGINE_WORKERS_MAX` (1 and 4), and veraPDF and the gateway follow their CPU; without them, `ENGINE_WORKERS` is a
fixed size. A worker pod may use `WORKER_MEMORY_LIMIT` (4 GiB) and 3 CPUs; a long document's per-page work is shared
over `PAGE_WORKERS` processes (3, from 50 pages). See
[scaling with the queue](../../deploy/k8s/README.md#scaling-with-the-queue).
