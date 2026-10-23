# Incident response

[Operations](README.md) › Incident response

What to do when something may have gone wrong with the service's security: an account someone else is using, a key
that's leaked, documents seen by people who shouldn't see them, a component that's behaving oddly. Written for the
person on the platform team who finds it, in the order to do things. The other two runbooks are
[rotating secrets](secret-rotation.md) and [rolling back](rollback.md).

Commands are run from a checkout of the repository with `deploy/k8s/deploy.env` and the settings files, as for
`scripts/deploy.sh`. `kubectl` commands use the cluster and namespace from `deploy.env`; the examples use the
namespace `pdf-remediation`.

> [!IMPORTANT]
> **Call the security office before you change anything you can't change back**, and keep a record from the first
> minute. Containing comes before understanding: the switches below are quick and undo cleanly.

## Who to call

Filled in by the campus before go-live, and kept up to date. Keep a copy somewhere that doesn't depend on the
service or the cluster.

| Who | When | Contact |
|---|---|---|
| `<campus information security office>` | First, for anything that may involve personal data or a leaked key | `<phone, email, ticket queue>` |
| `<service owner>` | Every incident | `<phone, email>` |
| `<cluster owner / platform on-call>` | Anything on the cluster or its nodes | `<phone, email>` |
| `<campus counsel / privacy office>` | Through the security office, when people may need to be told | `<contact>` |
| The AI provider (AWS, or OpenRouter) | A provider key leaked, or documents sent to the AI that shouldn't have been | `<account, support route>` |
| Adobe | An Adobe credential leaked, or documents sent to Adobe that shouldn't have been | `<account, support route>` |

Many incidents start with a [security alert](monitoring.md#security-alerts): its email says what was seen and links
to the audit log searched for it.

## 1. Start a record

Open a ticket or a shared document now and keep adding to it: **what was seen, when, by whom, and every step taken,
with the time** (US Eastern, or say which zone). The security office and anyone deciding on notices will need it, and
nobody remembers the order of things afterwards.

## 2. Contain

> **Changed 22 October 2026:** stopping personal API tokens, for everyone or one person (#518).

Quickest and least disruptive first. Each is undone the same way it was done.

| Step | How | What it does |
|---|---|---|
| **Hold the work** | Administration › Queues › **Pause** on each document queue | New work waits; work already started finishes. Sign-in codes are sent at once, never queued, so nobody is locked out |
| **AI or Adobe off for everyone** | Administration › Services: untick **Enabled** on the AI levels, Adobe, or both | Nobody can choose them; documents already waiting for them stay held while their queue is paused |
| **AI or Adobe off for a group** | Administration › Groups › the group: its services, or **local-only** on | That group's documents stay on the service's own servers |
| **One person** | Administration › People › Manage: **Sign out everywhere**, or **Disable account** (which also signs them out) | Their sessions end at once; a disabled account can't sign in again |
| **Scripts' API tokens** | Administration › Settings, **Downloads and scripts**: **API tokens for scripts** off (or a group's own switch, in its settings); one token: its owner revokes it in Settings, or disable the account | Every token stops at once (each use checks every level); switching back on lets the unexpired ones work again |
| **A whole group** | Administration › Groups › the group › **Disable group** | Every member is signed out and can't sign in |
| **Everyone signed out** | [A new `PORTAL_SECRET_KEY` and signing key](secret-rotation.md#everyone-signed-out-at-once) | Every session ends within 10 minutes; people sign in again with a code |
| **A key that may have leaked** | [Rotate it](secret-rotation.md) | The old value stops working |
| **The engine stopped** | `kubectl -n pdf-remediation annotate scaledobject/engine-worker autoscaling.keda.sh/paused-replicas="0"` (with scaling on), or `kubectl -n pdf-remediation scale deployment/engine-worker --replicas=0` (without) | No document is opened. Undo: remove the annotation (`autoscaling.keda.sh/paused-replicas-`), or `scripts/deploy.sh` |
| **Last resort: the service down** | `kubectl -n pdf-remediation scale deployment/portal-api deployment/web --replicas=0` | Nobody can sign in, upload or download. Undo: `scripts/deploy.sh` |

There's no switch that stops uploads alone: pausing the queues means nothing uploaded is processed, and disabling
groups, or taking the API down, stops uploads.

Outbound traffic is already limited to the hosts the service uses when `EGRESS_PROXY=enforce`
([deploy/k8s/README.md](../../deploy/k8s/README.md#outbound-traffic-the-egress-proxy)). If it's still `log`, setting
`enforce` and deploying is a containment step too.

## 3. Keep the evidence

Some of it deletes itself on a timer. Do these before anything is restarted, restored or rolled back.

1. **Stop the automatic deletions.**
   - The engine removes each job's folder (the uploaded PDF, its output and logs) 24 hours after the job. Stop its
     scheduler, which does only that:

     ```bash
     kubectl -n pdf-remediation scale deployment/engine-beat --replicas=0
     ```

     Undo once the evidence is copied: `scripts/deploy.sh`.
   - The portal deletes stored documents after the retention period (30 days unless changed). Raise it in
     Administration › Settings › How long things are kept, and write down the old values to set back.
2. **Copy the pods' logs.** Logs go when a pod is replaced. For each component involved (`portal-api`,
   `portal-worker`, `engine-api`, `engine-worker`, `litellm`, `egress-proxy`, `web`):

   ```bash
   kubectl -n pdf-remediation logs deployment/portal-api --all-containers --timestamps --since=168h > portal-api.log
   ```

   Add `--previous` for a pod that has restarted. The egress proxy's log lists every outside host each component
   reached. The ingress controller's log (in `kube-system` on RKE2) has every request's address.
3. **Export the audit log** for the period (with the [SIEM export](siem.md) on, the SIEM has it too): Administration › Reports › **Audit log**, set the dates, Export. Do the
   same for **Sign-ins** (failed attempts, code requests) and **Documents**. The export is itself recorded.
4. **Back up now**, and keep it apart from the routine backups (whose rotation would delete it):

   ```bash
   scripts/backup.sh --k8s --files --out <evidence folder>
   ```

5. **Copy the affected job folders** while they're there (the job ids are in the Documents report):

   ```bash
   kubectl -n pdf-remediation exec deployment/engine-api -- tar -C /data/jobs -cf - <job id> > job-<job id>.tar
   ```

6. **Note the versions running**: `kubectl -n pdf-remediation get deployments -o wide` (the images column), and the
   last lines of `deploy/k8s/deploy-history`.

Keep evidence where only the people handling the incident can read it: it contains documents and personal data.

## 4. Find the extent

> **Changed 22 October 2026:** documents sent with an API token.

The questions the security office will ask, and where the answers are:

| Question | Where |
|---|---|
| Which documents, whose, and when? | Reports › Documents, by group and dates; each document's owner, group, file name, pages, size and SHA-256 |
| Did any leave the service? | Reports › Documents, **Sent elsewhere**: which documents went to the AI provider or Adobe (each document's record lists the outside services it used) |
| Who signed in, and from where? | Reports › Sign-ins: each sign-in, failure and code request, with the address |
| What did administrators change? | Reports › Audit log: every change to people, groups, services, limits and settings, with who and when |
| Was a token used? | Each document sent with one is marked **sent by script**, with the token's name, and document lists filter on it; the audit log has `api_token.created` and `api_token.revoked` |
| What reached the internet? | The egress proxy's log |
| Which groups and people? | The reports above, filtered by group; Administration › People |

For a question the reports don't answer, read the database directly, read-only:

```bash
kubectl -n pdf-remediation exec -it statefulset/postgres -- psql -U portal -d portal
```

```sql
-- Documents from one group in a period, and the outside services each used
select j.id, u.email, j.filename, j.created_at, j.external_services
from jobs j join users u on u.id = j.user_id
where j.group_id = '<group id>' and j.created_at between '<from>' and '<to>' order by j.created_at;
```

## 5. Recover

1. **Rotate** every secret that may have been seen ([secret rotation](secret-rotation.md)): for a compromised pod,
   every secret that pod holds ([which pod sees which secret](../../deploy/k8s/README.md#which-pod-sees-which-secret)).
2. **Roll back** if a release caused it ([rollback](rollback.md)).
3. **Restore** data that was changed or deleted ([restoring](backups-and-migrations.md#restoring)): restoring one
   table's rows, or one group's files, is possible without restoring everything.
4. **Undo the containment** in reverse order: the engine scheduler and the retention settings (once evidence is
   copied), the queues, the services, then people and groups as the security office agrees.
5. **Watch** for a few days: the Sign-ins and Audit log reports, and Grafana.

## 6. Notify

**The campus decides** who is told and when, through its security office and counsel. That may include notices under
FERPA (student records), New York's breach notification law (General Business Law §899-aa), and, for a state entity,
State Technology Law §208. The platform team supplies the facts:

| Fact | From |
|---|---|
| What happened, when it started, when it was found, when it was contained | The record (step 1) |
| What data: documents, their kinds, how many, whose | Step 4 |
| How many people affected, and which groups or campuses | Step 4 |
| Whether data left the service, to whom, and the provider's terms for what it received | Step 4, and the provider's agreement |
| Whether the data was encrypted where it was exposed | Stored documents are encrypted in the file store; the engine's job folder (kept 24 hours) is as encrypted as the share it's on; traffic is encrypted in transit |
| What's been done, and what's still being done | Steps 2 and 5 |

## 7. Afterwards

Within a week: a short review with everyone involved. What happened, what helped, what didn't, without blame. Each fix
becomes a to-do with an owner, and this page is corrected where it was wrong or slow.
