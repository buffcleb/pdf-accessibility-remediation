# Resilience

[Deployment](../README.md) › [First cluster](README.md) › Resilience

What keeps working when something fails, on the cluster this guide builds, and the steps up when the service needs more.
Each step costs something to run; take the cheap ones now and the rest when the service has a stated level of
availability to meet.

## What survives what

| Failure | What happens on three nodes | Step up | Cost |
|---|---|---|---|
| **One node down** (patching, a fault) | The cluster carries on: two of three servers keep its database. Pods on that node start on the others within minutes. A drain lets the documents being remediated there finish first. When a node dies outright, its documents are started again once the queue gives them up: about 75 minutes (the engine's one-hour limit plus 15) | Two copies of the portal API, web front end and AI gateway, kept on different nodes, with a PodDisruptionBudget, so a drain never takes all of them at once | A little memory. **Worth doing for the POC** |
| **Two nodes down** | The cluster stops accepting changes until one is back; the service may stop | More server nodes doesn't help much here: patch one node at a time ([patching](os.md#patching)) | – |
| **The database's node down** | The service is down until the database starts on another node, a few minutes, **if its volume can be reached from there** (a storage question: [checklist](checklist.md#storage)) | A replicated database with automatic failover ([CloudNativePG](https://cloudnative-pg.io)), or a campus-run PostgreSQL | One or two more database copies; an operator to run |
| **The queue (Redis) down** | Documents wait; nothing is lost, the portal sends them again | Redis with Sentinel; or accept it: it restarts in seconds | Usually not worth it |
| **The file store (SeaweedFS) down** | Uploads and downloads fail until it's back | Replicated SeaweedFS volumes, or campus S3-compatible storage | Storage, and a migration |
| **A storage system down** | Whatever uses it stops: the storage team's resilience is the service's | – | The storage team's |
| **The floating address's node down** | The address moves to another node within seconds | – | – |
| **The AI provider down** | Documents are remediated without AI and say so | A second provider in the AI gateway | An agreement with another provider |
| **The whole cluster lost** | Rebuild from this guide, restore from backups | A rehearsed rebuild with a known time; a second site only if the service level needs it | Time to rehearse; a second cluster |

## The cheap steps, for the POC

- **Two copies of the stateless parts, on different nodes.** The portal API, the web front end and the AI gateway can
  run as two pods each, with anti-affinity (Kubernetes keeps them apart) and a PodDisruptionBudget (a drain waits rather
  than take both). Not a setting yet: ask the service's maintainers.
- **The cluster's database snapshots off the cluster.** RKE2 takes them every 6 hours on each server
  ([install](middleware.md#1-the-settings-on-the-first-server)). Copy them elsewhere: RKE2 can send them to
  S3-compatible storage (`etcd-s3` settings), or the storage team's backup can take
  `/var/lib/rancher/rke2/server/db/snapshots`.
- **Volumes reachable from every node.** The storage team's block storage must let a volume follow its pod to another
  node. Test it: [checklist](checklist.md#storage).
- **A rehearsal.** Drain and power off one node while a document is being remediated. The service should carry on, and
  the document finish.

## Backups

Two different things are backed up:

| What | How | Restores |
|---|---|---|
| **The service's data**: its databases and stored documents | The nightly backup (`BACKUPS=cronjob`) onto a share, and the one `deploy.sh` makes before each database change; or the storage team's snapshots of the volumes (`BACKUPS=external`) | The service's data, onto this cluster or a new one ([backups](../../operations/backups-and-migrations.md)) |
| **The cluster itself**: what's installed and its settings | RKE2's etcd snapshots | The cluster's own state after a control-plane disaster ([RKE2: backup and restore](https://docs.rke2.io/datastore/backup_restore)) |

With the storage team's backups taking the nightly backup's folder, keep two or three nights there
(`BACKUP_KEEP_DAILY=3`, `BACKUP_KEEP_WEEKLY=0`): their backups hold the history. Keep the settings files' secrets and
the file store's encryption key in 1Password: without that key, restored documents can't be read.

**Test a restore** before go-live, and after any large change: restore the newest backup into a scratch copy and look.
A backup no one has restored is a hope, not a backup.

## Rebuilding from nothing

If the cluster is lost, the way back is: three servers ([servers](os.md)), the cluster ([install](middleware.md)), the
add-ons, the service deployed with the same settings and secrets (from 1Password), then the data restored from the
latest backup. Rehearse it once on spare VMs, and write down how long it took: that's the service's recovery time
until something faster is built.
