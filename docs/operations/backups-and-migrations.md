# Backups and migrations

[Operations](README.md) › Backups and migrations

## Database migrations

The portal's database schema is managed with Alembic (`portal/migrations/`). Some migrations reshape data and can't be
undone automatically, so **back up before deploying a new version**.

- **Docker Compose:** the portal API applies migrations when it starts.
- **Kubernetes:** `scripts/deploy.sh` backs up the databases first, then runs the migrations as a one-off Job, and stops
  if they fail; the portal API doesn't migrate on start there.
- **Elsewhere:** run `python -m portal.cli migrate` once, with the portal's settings, before starting the new version.

Taking a release back, with its migrations (`scripts/deploy.sh downgrade`, or a restore from the backup made before
them), is in [rolling back](rollback.md).

## Backing up

```bash
scripts/backup.sh
```

`scripts/backup.sh` always backs up the portal database, and the AI gateway's database (spend logs, virtual keys)
unless `--no-litellm`. Flags add the rest:

| Flag | Adds |
|---|---|
| `--files` (`--files-prefix groups/<id>/` for one group) | Stored files: inputs, outputs, reports, tag maps. Read through the portal's own S3 settings, so it works with any S3 service |
| `--config` (`--config-file PATH` for more) | The settings files and `config/`. **Plaintext by default** (they hold secrets): keep backups somewhere protected |
| `--encrypt-config` | Encrypts the config archive: with age when `BACKUP_AGE_RECIPIENT` is set, otherwise OpenSSL AES-256 with `BACKUP_PASSPHRASE` |
| `--all` | `--files --config` |
| `--keep N` | Deletes all but the newest N backups afterwards |
| `--upload s3://bucket/prefix` | Copies the backup off the machine (`BACKUP_S3_*`, or the standard AWS variables or machine role) |
| `--k8s` | Back up a Kubernetes deployment (the cluster and namespace in `deploy/k8s/deploy.env`) |

Each run makes `backups/backup-YYYYmmdd-HHMMSS/` (or under `--out DIR`, or `BACKUP_DIR`): `portal.dump`,
`litellm.dump`, `files.tar`, `config.tar.gz[.age|.enc]`, a `MANIFEST.txt` (code version, migration, contents) and
`SHA256SUMS`. Folders are private to the user running it, a failed run leaves nothing behind, and two runs can't
overlap.

A nightly example:

```bash
0 2 * * * cd /srv/pdf-remediation && scripts/backup.sh --all --keep 14 --upload s3://my-backups/pdf-remediation >> /var/log/pdf-backup.log 2>&1
```

## Nightly backups on the cluster

`BACKUPS` in `deploy/k8s/deploy.env` decides how a Kubernetes deployment is backed up:

- **`cronjob`**: a CronJob (`90-backup.yaml`) backs up both databases and the stored files every night onto an NFS
  share, in the same folders `backup.sh` makes, so `restore.sh` restores from them. The portal alerts every super
  admin when none has finished for 26 hours (**Settings › Alerts**); every way a backup can fail (the share missing
  or full, a dump failing, the job not running) ends up there.
- **`external`** (the default): no job, and no backup alerts. Use it where the hosts are backed up another way (see
  [Relying on the hosts' own backups](#relying-on-the-hosts-own-backups)), or for a test cluster that needs none.

Either way `scripts/backup.sh --k8s` still works by hand, and `deploy.sh` runs it before every migration.

### Setting up the share (`cronjob`)

1. On the NAS, create a shared folder for the backups (20 GB is plenty to start; the stored files are most of it).
2. Allow NFS access to it from every node, by the address the nodes reach the NAS from. With a separate storage
   network that's the nodes' storage-network addresses, not the ones Kubernetes lists (`ip route get NAS_ADDRESS` on
   a node shows it, as `src`). Simplest: copy the NFS rule of the share the job folder already mounts from. On a
   Synology: *Control Panel › Shared Folder › Edit › NFS Permissions*, that address or subnet, **Squash: Map all users
   to admin**, so the job (uid 10001) can write.
3. Turn on snapshots of that share on the NAS if it has them (or copy it elsewhere): the share is off the cluster, but
   not off the NAS.
4. In `deploy/k8s/deploy.env`:

   ```bash
   BACKUPS=cronjob
   BACKUP_NFS_SERVER=nas.example.lan
   BACKUP_NFS_PATH=/volume1/pdf-backups
   ```

   Optional: `BACKUP_SCHEDULE` ("0 2 * * *"), `BACKUP_TIMEZONE` (America/New_York), `BACKUP_KEEP_DAILY` (14),
   `BACKUP_KEEP_WEEKLY` (8: the newest backup of each of that many weeks, besides the nightly ones), `BACKUP_FILES`
   (on; off backs up the databases only), `BACKUP_SIZE` (20Gi, the volume's nominal size), `BACKUP_NFS_VERSION`
   (4.1, as a Synology's NFS volumes mount).
5. Deploy. To run one now instead of waiting for the night:

   ```bash
   kubectl -n NS create job --from=cronjob/backup backup-now
   ```

   `kubectl -n NS logs job/backup-now --all-containers` shows both steps; the backup is on the share.

**Moving the share later:** a volume's server, path and options can't change once it exists (Kubernetes refuses:
"spec.persistentvolumesource is immutable"). Delete the backup jobs, the claim and the volume, then deploy again;
nothing on the share is touched:

```bash
kubectl -n NS delete job -l app=backup
kubectl -n NS delete pvc backups
kubectl delete pv NS-backups
```

(A job made by hand before this label existed is deleted by name: `kubectl -n NS delete job backup-now`.)

If a backup stays at `Init:0/1`, `kubectl -n NS get events` names the cause; "access denied by server" means the
path doesn't exist on the NAS or its NFS rules don't include the nodes.

Two things are deliberately **not** on the share: the deployment's settings files (`deploy/k8s/*.env`, which hold
the secrets) and the SeaweedFS encryption key. Keep both in a password manager. Without the key, restored files
can't be read.

### Relying on the hosts' own backups

Snapshots of the hosts or their storage (VM snapshots, storage-array snapshots) can replace the nightly job, if:

- **they are snapshots, not copies of files**: Postgres and SeaweedFS keep their data across many files that change
  while they run; a file-by-file copy of a running database may not restore. A snapshot taken at one instant restores
  like a power cut, which both recover from;
- **they cover the cluster's volumes**: wherever the storage class keeps the database's, SeaweedFS's and the job
  folder's volumes, not only the nodes' system disks;
- **the settings files and the SeaweedFS encryption key are kept** (a password manager);
- **a restore has been tried** once, into a scratch copy.

What they don't give: restoring one group's files, or reading one table, without rolling everything back. A dump
from `backup.sh` (by hand, or before each migration) still does.

### Trying a restore

Once, and after any big change: restore the newest backup into a scratch namespace or a local stack, and look.

```bash
scripts/restore.sh --from /path/to/share/backup-YYYYmmdd-HHMMSS --db --files --dry-run
```

Then without `--dry-run` against the scratch stack. Sign in, open a document, download its files.

## Restoring

`scripts/restore.sh` checks the checksums first and restores only what you name:

```bash
scripts/restore.sh --from backups/backup-20261007-020000 --db --dry-run
```

```bash
scripts/restore.sh --from backups/backup-20261007-020000 --db --files
```

- After restoring the portal database on the cluster, run `scripts/deploy.sh` once: it gives the portal's own account
  (`portal_app`, #337) the rows of the restored tables again, which a backup from before that account existed lacks.
- `--db` / `--litellm` replace a database. The current one is dumped to `backups/pre-restore-<time>/` first, and the
  services using it are stopped and started again (scaled to 0 and back with `--k8s`).
- `--files` puts back stored files, keeping ones that already exist unless `--overwrite-files`.
- `--config` unpacks config into `backups/restored-config-<time>/` to compare; `--config-in-place` puts it back,
  keeping the current files as `*.pre-restore-<time>`. Encrypted config needs `BACKUP_AGE_IDENTITY` or
  `BACKUP_PASSPHRASE`.
- It asks you to type `restore` unless `--yes`; `--dry-run` changes nothing.
- `--from s3://…` downloads the backup first (not with `--k8s`: download it, then restore from the folder).

**Reading one table, or a few rows** (the database restores whole, so take what you need by hand):

```bash
scripts/restore.sh --from backups/backup-20261007-020000 --sql --table jobs --data-only
```

- `--sql` writes the dump as plain SQL into `backups/sql-<backup>-<time>/` (`portal.sql`; `litellm.sql` with
  `--litellm`) and touches nothing live. `--table NAME` (repeatable) keeps only those tables; `--data-only` leaves out
  the schema.
- `--into-scratch` restores the backup into a separate database, `portal_restore`, beside the live one, to query and
  copy from. Drop it afterwards (`psql -U postgres -c 'drop database portal_restore'`).
- Mind the audit log and the page ledger: they only accept new rows by design.
- One group's **files** can be restored on their own: `--files --files-prefix groups/<group id>/`.

**Moving data between deployments** (for example, from a local test stack to a server): back up with `--all` on one,
and restore with `--db --files` on the other.

## Other deployments

Both scripts run against the local Compose stacks by default, or a Kubernetes deployment with `--k8s`. For anything
else, set `PORTAL_DB_EXEC` / `LITELLM_DB_EXEC` (how to run `pg_dump` and `pg_restore`) and `PORTAL_CLI` (how to run
`python -m portal.cli` with the portal's settings); see the top of each script.
