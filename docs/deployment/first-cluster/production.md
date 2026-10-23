# From POC to production

[Deployment](../README.md) › [First cluster](README.md) › From POC to production

Proofs of concept tend to become production without being rebuilt. This guide plans for that: the choices that can't
be changed later are made at install ([decided before install](checklist.md#decided-before-install)), so nothing
below should need the cluster rebuilt. This page is what turns the proof of concept into a service people rely on.

## Before people outside the pilot use it

> **Changed 22 October 2026:** malware scanning (#455) and a supported ingress controller (#460) are built.

**Security** (from the campus security office's review of the service):

| Item | Status |
|---|---|
| The AI provider set to AWS Bedrock under the campus's agreement, and the regression set run against it | To do with the campus's AWS account. **Ask its AWS team early** to confirm the models are enabled on that account (Claude Haiku 5.5, through `us.` profiles): AWS can hold new models back per account, and approval can take days |
| Encryption between nodes (WireGuard) | In this guide's install |
| Row-level security in the database | Done |
| Outbound traffic restricted for the portal, AI gateway and Adobe worker | Done (#453): `EGRESS_PROXY=log`, then `enforce`; and ingress denied by default |
| Runbooks: incidents, rotating secrets, rolling back | Done (#454): [incident response](../../operations/incident-response.md), [rotating secrets](../../operations/secret-rotation.md), [rolling back](../../operations/rollback.md); each rotation and rollback still to be tried once on the lab |
| Malware scanning of uploads, or the risk accepted in writing | Built (#455): `MALWARE_SCAN=clamav` ([malware scanning](../../operations/malware-scanning.md)); off, the [CISO review](../../evaluation/reviews/2026-10-19-ciso-review.md) records the risk accepted. Decide which |
| Audit log to the campus SIEM; alerts on repeated failed sign-ins | Built (#456): [the SIEM export](../../operations/siem.md) (`PORTAL_SIEM_MODE`, with the collector's address from the security office) and [security alerts](../../operations/monitoring.md#security-alerts); to be tried against the campus collector |
| The engine's keys: required, and a limited AI gateway key | Done (#457): `deploy.sh` makes the limited key |
| A supported ingress controller | Built (#460): `INGRESS_CONTROLLER=traefik`, with [Traefik's settings for the cluster](middleware.md#traefiks-settings-for-the-cluster); a cluster on ingress-nginx [moves to Traefik](middleware.md#moving-a-cluster-from-ingress-nginx-to-traefik) |
| A "restricted" group type for offices that may send FERPA or HR records (no AI or Adobe) | Decide with the security office |

**Running it:**

- [ ] **An owner** for the service, and for the cluster, named and written down, with who's called out of hours (if
  anyone).
- [ ] **Alerts** go to a team mailbox or ticket queue, not one person.
- [ ] **A restore tested**: the newest backup restored into a scratch copy and checked
  ([backups](../../operations/backups-and-migrations.md#trying-a-restore)). Repeat after any big change, and yearly.
- [ ] **A node failure tested**: one node drained and powered off during a test document; the service carries on
  ([resilience](resilience.md)).
- [ ] **Secrets in 1Password**, in a vault for this environment, with named access; the files on administrators'
  machines removed.
- [ ] **Changes go through the campus's change process**: who may deploy, when, and how it's announced. `deploy.sh`
  backs up before each database change; rolling back is in [its runbook](../../operations/rollback.md).
- [ ] **Certificates** renew automatically (cert-manager), or the renewal date is in a shared calendar.
- [ ] **Patching** has a schedule: OS monthly, one node at a time ([patching](os.md#patching)); RKE2 at least each
  supported minor version ([upgrading](middleware.md#upgrading-rke2)).
- [ ] **The engine on nodes of its own** (`ENGINE_NODES`, [scaling](scaling.md#4-the-pdf-engine-on-its-own-nodes)):
  one or two agent nodes, so documents are never opened beside the control plane.
- [ ] **A campus registry mirror**, so a Docker Hub limit or outage can't stop pods starting.
- [ ] **Capacity reviewed** after the pilot: Grafana's figures for documents a day, queue wait times and peak memory,
  against [scaling](scaling.md).
- [ ] **Retention** agreed and set: how long documents and records are kept (Administration › Settings), and that it
  matches campus records policy.
- [ ] **Campus sign-in (SSO)**: designed, on hold. Until then, sign-in is a code by email.

## What doesn't need redoing

Because the install decisions were made for production:

- the cluster's addresses, names and certificates stay;
- the data stays where it is: the storage team's volumes and backups;
- more capacity is more nodes or larger ones, added without downtime ([scaling](scaling.md));
- other applications can move onto the cluster later ([more applications](more-applications.md)).

If any decision on the [checklist](checklist.md#decided-before-install) was made differently for the POC, fix it before
production. Each is harder to change the longer the service runs.
