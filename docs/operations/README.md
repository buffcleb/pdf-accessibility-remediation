# Operations

Configuring and running the service once it's installed. Installing it is covered in
[Deployment](../deployment/README.md); what super admins do in the portal is in the
[super admin guide](../user-guide/super-admins/README.md).

## Contents

1. [Configuration](configuration.md): where settings live, the main ones, and what's changed in the app instead
2. [Settings reference](settings-reference.md): every setting, with its default and what it does,
   generated from the code
3. [AI models](ai-models.md): the gateway, local and cloud models, review models, OpenRouter profiles, notation, OCR
   correction, the classifier, verification settings
4. [Email and branding](email-and-branding.md): mail providers, SPF/DKIM/DMARC, the break-glass sign-in code, branding
5. [Adobe Autotag](adobe-autotag.md): credentials, limits, the fake Adobe for testing
6. [Backups and migrations](backups-and-migrations.md): `backup.sh`, `restore.sh`, database migrations
7. [Monitoring and troubleshooting](monitoring.md): health checks, logs, capacity, common problems
8. [Limits](limits.md): every limit on a document, a person or a request, its default, where to change it, and
   what happens past it
9. [CI on your own runners](ci-runners.md): running the tests on a machine of your own, not on GitHub's billed
   minutes (and why not on the service's cluster); which jobs run for which files
10. [Incident response](incident-response.md): who to call, containing, keeping the evidence, finding the extent,
   recovering, notifying
11. [Rotating secrets](secret-rotation.md): every password and key, `rotate-secret.sh`, a schedule
12. [Rolling back](rollback.md): the earlier images, the database with them, checking afterwards
13. [The audit log to a SIEM](siem.md): syslog over TLS, HTTPS or a pull API; what's sent; what the pods log
14. [Malware scanning](malware-scanning.md): optional ClamAV for uploads and help-desk attachments; what's refused,
    what runs, turning it on, the risk when it's off

## At a glance

| What | Where |
|---|---|
| Every setting | [Settings reference](settings-reference.md): defaults and what each does, for every part |
| Engine settings | `.env` (local), or the engine's secret in Kubernetes; every setting is in `.env.example` and `app/config.py` |
| Portal settings | `.env.portal` (local), or the portal's secret; see `portal/config.py` |
| AI models | `config/litellm.yaml`, with hosts and keys from the engine's settings |
| Things super admins change in the app | Branding, messages, limits, retention, services, AI levels: stored in the database, audited |
| Health | `GET /api/v1/health` on the engine and on the portal |
| Backups | `scripts/backup.sh` (add `--k8s` for Kubernetes) |
| Something wrong | [Incident response](incident-response.md), [rotating secrets](secret-rotation.md), [rolling back](rollback.md) |
| Tests | [tests/README.md](../../tests/README.md) |
