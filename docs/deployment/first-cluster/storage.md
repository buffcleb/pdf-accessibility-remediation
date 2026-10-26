# Storage

[Deployment](../README.md) › [First cluster](README.md) › Storage

For the storage and OS teams: what the service keeps where, what kind of storage each part needs, and the options when
only some kinds are on offer. New to Kubernetes? The [start page](README.md#kubernetes-in-one-page) explains the terms
in a page.

## Two kinds of storage, and neither shares a disk between servers

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../images/diagrams/storage-kinds-dark.svg">
  <img src="../../images/diagrams/storage-kinds.svg" alt="A database's block disk is attached to one server at a time, the one running its pod, and the storage driver moves it when the pod moves; the engine's job folder is one NFS share that every server mounts at once.">
</picture>

| | The databases | The engine's job folder |
|---|---|---|
| What | **Postgres** (people, groups, records), **Redis** (the queue), **SeaweedFS** (the stored documents, encrypted), and Prometheus and Grafana (monitoring history) | Documents being remediated, for up to 24 hours |
| Kind | **Block storage**: one disk (volume) for each | **Shared storage**: one folder, over NFS or similar |
| Used by | **One server at a time** (Kubernetes calls it ReadWriteOnce) | Several pods on several servers at once (ReadWriteMany) |
| When a pod moves to another server | The cluster's storage driver **detaches the disk from the old server and attaches it to the new one** | Nothing changes: every server mounts the folder |
| Size to start | Postgres 10 GB, Redis 1 GB, documents 50 GB, monitoring 20 GB | 50 GB |

> [!NOTE]
> **No disk is ever mounted by two servers at once.** The hard part of shared storage, one SAN volume used by several
> servers together (which needs a cluster filesystem such as GFS2 or OCFS2, and fencing), isn't needed here. Each
> database disk belongs to one server at a time, and the storage driver moves it. The one folder that is shared is
> plain NFS, which is built for sharing.

**Retain.** The block storage class should keep a disk when its claim in Kubernetes is deleted (a **Retain** reclaim
policy). With **Delete**, the default for many storage classes, deleting the claim also destroys the disk and its data,
whether by mistake, a wrong `kubectl delete`, or the whole namespace removed. With Retain the disk stays and can be
attached again. The job folder doesn't need it: it holds only work in progress.

**Block storage, not NFS, for databases.** Postgres and the others rely on writes reaching the disk when they say so
(`fsync`). NFS can acknowledge writes it hasn't made durable, and a database on it can be corrupted by a network or
server failure.

## Where block storage can come from

In order of preference:

1. **A storage driver for what the campus already runs.** Most hypervisors and SANs have a Kubernetes (CSI) driver:
   vSphere, Proxmox, NetApp, Pure, Dell, iSCSI arrays. It creates a disk for each volume on request and attaches it to
   whichever server needs it. The storage team installs it and names the storage class.
2. **No shared storage at all: Longhorn.** SUSE's storage system for RKE2 uses a plain **second virtual disk on each of
   the three servers** (for example 100–200 GB each) and keeps a copy of every volume on two or three of them, so a
   volume follows its pod to another server and survives one being lost. The OS team adds the disks; the middleware
   team installs Longhorn ([longhorn.io](https://longhorn.io)). Good when there's no driver for the campus's storage.
3. **The database outside the cluster** (below), when neither suits.

Avoid **a server's local disk on its own** (a "local path" class): a volume then can't move, so if that server is down,
whatever's on it is down until it's back.

## When only NFS is on offer

| Part | What to do |
|---|---|
| **Postgres** | Outside the cluster: a campus PostgreSQL service, or a Postgres VM the OS team runs (with its backups). The deployment doesn't support this yet: to-do #465 |
| **The stored documents** | Campus S3-compatible object storage (StorageGRID, ECS, MinIO and the like): the service talks plain S3 to any address. Also to-do #465 |
| **Redis** (the queue) | Can live on NFS, or keep no files at all: if the queue is lost, the portal sends the documents again |
| **Monitoring** (Prometheus) | Prometheus doesn't support NFS. Keep no history across restarts, or turn monitoring off |
| **The job folder** | NFS, as planned |

What an outside PostgreSQL needs to provide: PostgreSQL 16; two databases (`portal` and `litellm`); three logins
(`portal`, which owns the tables and runs the updates at each deploy; `portal_app`, the service's everyday login,
limited to rows; `litellm`); access from the three servers on port 5432, with TLS; and backups by whoever runs it.
About 10 GB to start.

## The shared folder (NFS)

- An export the three servers (or their storage network's addresses, if there's a separate one) can mount, NFSv4.1.
- The engine runs as user **10001**: the export must let it write. A share that maps every user to one account
  (all-squash to an owner that can write), or a folder owned by 10001, both work. The service checks when it starts
  and says so if it can't write.
- An NFS provisioner in the cluster (csi-driver-nfs, or nfs-subdir-external-provisioner) makes a folder on it for the
  service.

## Growing a volume

> **New 23 October 2026** (#592): `scripts/deploy.sh` grows a volume when its size setting goes up.

Volumes don't grow by themselves. When one fills (the "A volume is nearly full" alert, or Grafana's volumes
dashboard), raise its size in `deploy/k8s/deploy.env` and run `scripts/deploy.sh`.

| Setting | Volume (its claim) | Kind | To start |
|---|---|---|---|
| `DB_SIZE` | Postgres: `data-postgres-0` | StatefulSet | 10Gi |
| `REDIS_SIZE` | Redis (the queue): `data-redis-0` | StatefulSet | 1Gi |
| `FILES_SIZE` | SeaweedFS (the stored documents): `data-seaweedfs-0` | StatefulSet | 50Gi |
| `JOBDATA_SIZE` | The engine's job folder: `jobdata` | Claim | 50Gi |
| `PROMETHEUS_SIZE` | Prometheus's history: `prometheus-data` | Claim | 20Gi |
| `GRAFANA_SIZE` | Grafana: `grafana-data` | Claim | 1Gi |
| (none) | ClamAV's signatures: `clamav-db`, with malware scanning on | Claim | 2Gi, fixed: they need well under 1 GB |
| `BACKUP_SIZE` | The backup share: `backups` | Made by hand | Only a label: the NAS share's own size is what counts |

**The supported way.** Raise the setting (sizes such as `100Gi`) and run `scripts/deploy.sh`. Before it changes
anything it compares each setting with the claim on the cluster and lists what will grow beside the rest of its plan,
for example:

```text
  volumes to grow (docs/deployment/first-cluster/storage.md#growing-a-volume):
    data-seaweedfs-0: 50Gi to 100Gi (FILES_SIZE)
    statefulset/seaweedfs made again for 100Gi, its pod and volume left running (delete --cascade=orphan)
```

Once you answer yes (or straight away with `-y`), it:

1. checks the claim's storage class allows growing (`allowVolumeExpansion: true`), and stops before changing anything
   if it doesn't ([below](#when-the-class-cant-grow-a-volume));
2. asks for the new size on the claim;
3. for Postgres, Redis and SeaweedFS, deletes the StatefulSet with `--cascade=orphan`, which leaves its pod and volume
   running, so the apply that follows makes it again with the new size (Kubernetes refuses to change a StatefulSet's
   volume size in place: that's why raising one of these used to break the deploy);
4. waits up to two minutes for the claim to report the new size, and says either "its filesystem too: nothing to
   restart", or that the filesystem grows only when its pod starts again, with the command to restart it (said again
   at the end of the deploy).

A size the same as the claim's, written another way (`51200Mi` for `50Gi`), changes nothing.

**While a volume grows.** The storage system enlarges the disk, then the node enlarges the filesystem on it. Most
drivers do both while the pod runs, with no interruption. Some grow the filesystem only when the volume is next
mounted: the claim then shows the condition `FileSystemResizePending`, and the pod needs a restart (a few seconds
down for Postgres, Redis or SeaweedFS):

```bash
kubectl -n pdf-remediation get pvc data-seaweedfs-0 -o jsonpath='{.status.capacity.storage} {.status.conditions[*].type}{"\n"}'
```

```bash
kubectl -n pdf-remediation rollout restart statefulset/seaweedfs
```

**Volumes never shrink.** Kubernetes can't make a volume smaller. A size below the claim's stops the deploy before
it changes anything, and says the size to set back. To use less space, move to a new, smaller volume with a backup and
a restore, as below.

### By hand, when deploy.sh can't be used

The same steps, for one volume (here the stored documents, to 100Gi; the namespace is `NAMESPACE` in `deploy.env`):

1. The claim's class, and whether it allows growing:

   ```bash
   kubectl -n pdf-remediation get pvc data-seaweedfs-0 -o jsonpath='{.spec.storageClassName}{"\n"}'
   ```

   ```bash
   kubectl get storageclass CLASS -o jsonpath='{.allowVolumeExpansion}{"\n"}'
   ```

   `true` is needed. Anything else: see [below](#when-the-class-cant-grow-a-volume).
2. The new size on the claim:

   ```bash
   kubectl -n pdf-remediation patch pvc data-seaweedfs-0 --type=merge -p '{"spec":{"resources":{"requests":{"storage":"100Gi"}}}}'
   ```

3. For Postgres, Redis or SeaweedFS only, the StatefulSet removed without its pod and volume:

   ```bash
   kubectl -n pdf-remediation delete statefulset seaweedfs --cascade=orphan
   ```

4. The same size in `deploy.env` (`FILES_SIZE=100Gi`), then `scripts/deploy.sh`, which makes the StatefulSet again
   and finds nothing left to grow. Until it runs, nothing restarts a crashed Postgres, Redis or SeaweedFS pod, so
   don't leave it long.
5. Watch the claim as above, and restart the pod if it says `FileSystemResizePending`.

### When the class can't grow a volume

Some classes don't allow it (`allowVolumeExpansion` unset or `false`); many NFS provisioners are like this. For the
job folder on NFS that rarely matters: the size is a label, and the share's own free space is what counts. For a
database volume:

- Ask whoever runs the storage whether the class can allow it. For many CSI drivers it's one line on the class
  (`allowVolumeExpansion: true`, changed with `kubectl patch storageclass`), and the class's volumes can grow from then
  on.
- Otherwise, move the data to a bigger, new volume: [back up](../../operations/backups-and-migrations.md#backing-up)
  (`scripts/backup.sh`), remove the StatefulSet and its claim (with a **Retain** class the old disk stays, as a
  fallback), set the bigger size in `deploy.env`, run `scripts/deploy.sh` to make an empty volume, and
  [restore](../../operations/backups-and-migrations.md#restoring) (`scripts/restore.sh --db`, `--files` for the stored
  documents). Try it on a test copy first ([trying a restore](../../operations/backups-and-migrations.md#trying-a-restore)).

`scripts/deploy.sh` says which class stopped it and sets nothing; set the size back until the class allows growing.

## Backups

Two choices ([backups](../../operations/backups-and-migrations.md)):

- **The storage team's snapshots** of the volumes (`BACKUPS=external`): **snapshots, not file copies** (a file-by-file
  copy of a running database may not restore), covering the database volumes, not only the servers' system disks.
- **The service's nightly backup** to an NFS share (`BACKUPS=cronjob`): a dump of the databases and the stored files.
  If the storage team's backups take that folder, keep two or three nights there (`BACKUP_KEEP_DAILY=3`,
  `BACKUP_KEEP_WEEKLY=0`): their backups hold the history.

Either way, keep the file store's encryption key in the password manager: restored documents can't be read without it.

## Who runs what as root

Like any Kubernetes cluster, the node software runs as root: RKE2 itself (the kubelet and container runtime), the
network plugin, and the storage driver's part on each server, which logs in to the storage, formats a new disk and
mounts it. That's what lets the cluster move disks between servers. Attaching a disk at the storage system is done by
the driver's controller through the storage's API, with an account limited to that.

The service's own pods run as ordinary users with every extra privilege dropped, can't mount anything, and are handed
volumes already mounted. Privileged pods belong only in the system namespaces (RKE2's and the storage driver's): the
service's namespace refuses them (Pod Security "baseline", enforced). More on the
[servers page](os.md#what-runs-as-root).

## Checklist

The storage checklist is on the [checklists page](checklist.md#storage).
