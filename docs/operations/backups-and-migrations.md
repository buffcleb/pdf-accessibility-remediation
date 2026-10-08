# Backups and migrations

[Operations](README.md) › Backups and migrations

## Database migrations

The portal's database schema is managed with Alembic (`portal/migrations/`). Some migrations reshape data and can't be
undone automatically, so **back up before deploying a new version**.

- **Docker Compose:** the portal API applies migrations when it starts.
- **Kubernetes:** `scripts/deploy.sh` backs up the databases first, then runs the migrations as a one-off Job, and stops
  if they fail; the portal API doesn't migrate on start there.
- **Elsewhere:** run `python -m portal.cli migrate` once, with the portal's settings, before starting the new version.

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

Each run makes `backups/backup-YYYYmmdd-HHMMSS/` (or `--out DIR`): `portal.dump`, `litellm.dump`, `files.tar`,
`config.tar.gz[.age|.enc]`, a `MANIFEST.txt` (code version, migration, contents) and `SHA256SUMS`. Folders are private
to the user running it, a failed run leaves nothing behind, and two runs can't overlap.

A nightly example:

```bash
0 2 * * * cd /srv/pdf-remediation && scripts/backup.sh --all --keep 14 --upload s3://my-backups/pdf-remediation >> /var/log/pdf-backup.log 2>&1
```

## Restoring

`scripts/restore.sh` checks the checksums first and restores only what you name:

```bash
scripts/restore.sh --from backups/backup-20261007-020000 --db --dry-run
```

```bash
scripts/restore.sh --from backups/backup-20261007-020000 --db --files
```

- `--db` / `--litellm` replace a database. The current one is dumped to `backups/pre-restore-<time>/` first, and the
  services using it are stopped and started again (scaled to 0 and back with `--k8s`).
- `--files` puts back stored files, keeping ones that already exist unless `--overwrite-files`.
- `--config` unpacks config into `backups/restored-config-<time>/` to compare; `--config-in-place` puts it back,
  keeping the current files as `*.pre-restore-<time>`. Encrypted config needs `BACKUP_AGE_IDENTITY` or
  `BACKUP_PASSPHRASE`.
- It asks you to type `restore` unless `--yes`; `--dry-run` changes nothing.
- `--from s3://…` downloads the backup first (not with `--k8s`: download it, then restore from the folder).

**Moving data between deployments** (for example, from a local test stack to a server): back up with `--all` on one,
and restore with `--db --files` on the other.

## Other deployments

Both scripts run against the local Compose stacks by default, or a Kubernetes deployment with `--k8s`. For anything
else, set `PORTAL_DB_EXEC` / `LITELLM_DB_EXEC` (how to run `pg_dump` and `pg_restore`) and `PORTAL_CLI` (how to run
`python -m portal.cli` with the portal's settings); see the top of each script.
