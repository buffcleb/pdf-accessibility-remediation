# System

[Guide for super admins](README.md) › System

**Administration → System** shows how the service itself is doing, from its monitoring (Prometheus). It's there
when the deployment includes monitoring (`MONITORING=on`, the default; see
[Operations › Monitoring](../../operations/monitoring.md#prometheus-and-grafana)); otherwise the page says so.

It has three tabs: **Now**, **Over time** and **Audit export**. **Open Grafana** is beside them.

## Now

### Documents and engine

Eight figures, refreshed every minute:

| Figure | What it is |
|---|---|
| Documents not finished | Every document waiting, sent to the engine or being processed |
| Oldest waiting | How long the oldest of them has been in the queue |
| Engine documents in progress | Documents the engine's workers are processing now |
| Engine places | How many they could process at once (workers × documents each) |
| Waiting for an engine worker | Documents sent to the engine that no worker has taken yet: every place is in use |
| Pods answering | The service's pods whose metrics Prometheus is reading |
| Restarts in the last hour | Pods restarted (a crash, a liveness probe, memory) |
| Alerts firing | Problems Prometheus has found; shown red when there are any |

### Capacity

CPU and memory in use against what the nodes have, for example **7.2** of 24 cores:

| Figure | What it is |
|---|---|
| CPU in use, all nodes | Everything running on the nodes, this service and anything else, against all their cores |
| Memory in use, all nodes | The same for memory, in GiB |
| This service's CPU | This service's own pods, against all the nodes' cores |
| This service's memory | The same for memory |

The gap between the first two and what the nodes have is the environment's headroom: what's left for the engine to
scale into. If the nodes are busy with other things, the engine may not get room even below `ENGINE_WORKERS_MAX`.

### Pods

Every part of the service (each deployment and stateful set) has a row, refreshed every 15 seconds, with a box for
each of its pods and an outline for each pod it could still have: from its minimum to its maximum when it scales
with the work (the engine's workers, the music reader, veraPDF), otherwise its usual number.

| Box | Means |
|---|---|
| ▶ green | Working: processing a document (or the portal worker's pass, or a picture of music) |
| – teal | Idle: ready, with nothing to do |
| ✓ green | Ready (a pod that doesn't say whether it's busy: the APIs, the database, the web app) |
| … amber | Starting: not ready yet, within its first 10 minutes |
| ? amber | Not ready, after that |
| ! red | Failing: crash-looping, its image can't be pulled, or its liveness probe failed in the last 10 minutes |
| × grey | Stopping: being removed (scaling down, a new release) |
| dashed outline | Room to scale; amber when the row is below its minimum |

Under each name: how many pods it may have and wants, and for the workers what's waiting in their queue and how
long the oldest document has waited. A row is marked **amber** while it starts pods (fewer ready than it wants),
and **red** when documents have waited 15 minutes or more while it's already at its maximum: the engine needs a
higher `ENGINE_WORKERS_MAX`, or the nodes more room.

Choose a box for that pod's details: its age and node, whether it's ready, its restarts and why the last one
happened (`OOMKilled`: it ran out of memory), its liveness and readiness probe failures in the last hour, its CPU
and memory against its limits, and the documents it's working on. Two parts open when you choose them:

- **About** the workload: what it does and what stops working without it, where it comes from (its upstream
  project, or the file in this project that builds it), and the image it runs, with its **version**, its
  **digest** and whether it's the **pinned** one (below).
- **kubectl commands** for that pod, with its real name and namespace, each with a **Copy** button: its log (as
  it's written, the last hour, the run before a restart), a look at it (describe, events, CPU and memory, its
  rollout and its autoscaler), and reaching it with `port-forward`. The commands that change something (restart,
  delete the pod, scale, a shell inside it) are listed apart. The portal runs none of them: they're for someone with
  `kubectl` access to the cluster.

#### Versions

> **New 22 October 2026:** each pod's version, digest and pin, and the **Versions** table.

Under the rows, **Versions** lists every workload's release in one table, for checking against a security advisory
at a glance (an advisory names a release, such as `litellm 1.107.0`, and often the images' digests). Each pod's
**About** says the same for each of its containers, with the full digest.

| Column | What it says |
|---|---|
| Release | The release, from the image's tag: **LiteLLM 1.105.0**, **ClamAV 1.5.4**. This service's own images (engine, portal, web, music and formula readers) say the **commit** they were built from (`scripts/build.sh` tags them with it). PostgreSQL and Redis, whose tags name only the major release (`postgres:16-alpine`), say the release **as they report it** to monitoring: **PostgreSQL 16.15 (as it reports)** |
| Digest | The image as running, shortened (`sha256:` and 12 characters). The full digest is in the pod's **About** |
| Pinned | Whether that's the digest the deployment pins (`scripts/deploy.sh` pins every image that isn't this service's own). **Not the pinned digest** is in bold: the pod runs something other than what was deployed |

A workload part-way through a new release has a row for each release while both run, with the number of pods on
each. Everything here comes from what Kubernetes and the exporters already tell Prometheus: the portal asks no
program and no registry, so it can't say when a release was published.

## Over time

Charts for the last **6 hours**, **day** or **week**: documents not finished (by status), documents finished per
hour (completed and failed), the engine's documents in progress against its places, what's waiting in Redis (the
engine's queue, Adobe's, the music reader's pages), CPU and memory by service, each node's CPU and memory in use
(everything on it, as a share of its size), this service's CPU and memory by node, all the nodes together (CPU and
memory as a share of their capacity), and the portal's requests per minute (by answer: 2xx, 4xx, 5xx). Each chart shows at most eight series, the busiest; the arrow keys read the values when
a chart has focus.

Reading them together: documents waiting while the engine's places are all in use means the engine is at its size
(with scaling on, `ENGINE_WORKERS_MAX`); documents waiting with places free points at the engine or its queue. A
service whose memory climbs to its limit is restarted, and shows in Restarts.

A node near 100% is full: pods scheduled there wait or are moved, and the node's other workloads compete with this
service's.

## Audit export

Where the audit log is sent (a SIEM, by syslog or HTTPS), how far it has got, and its last error if a send failed.
When the export is off, the tab says so. Setting it up: [Operations › SIEM](../../operations/siem.md).

## Grafana

**Open Grafana** opens it in a new tab: the same figures and more, in as much detail as you like, with a ready-made
**PDF Remediation** dashboard. There's no separate Grafana account: choosing Open Grafana signs you in for eight hours,
and only while you're a super admin (taking the role away, or disabling the account, ends it at the next click).
Coming to `/grafana/` without having opened it from here brings you back to this page.

Dashboards you make in Grafana are kept. The **PDF Remediation** dashboard itself comes with the deployment: changes
to it are replaced at the next deploy, so save a copy (Save as) to keep your own version.

## Alerts

Prometheus's alerts go where the portal's own do: **Needs attention**, and an email to every super admin when one
starts, a reminder while it lasts and a note when it clears (times in [Settings › Alerts](settings.md)). Each comes
with what to do. They are:

| Alert | When |
|---|---|
| A pod keeps crashing | It's been crashing and restarting for 10 minutes |
| A pod has restarted several times in the last hour | Three restarts or more in an hour |
| A pod hasn't been ready for 15 minutes | Running, but its readiness check has failed for 15 minutes |
| A service is running fewer pods than it should | For 15 minutes |
| Prometheus can't read a service's metrics | For 10 minutes |
| A pod is near its memory limit | Above 90% of it for 15 minutes |
| A volume is nearly full | Less than 10% left for 30 minutes |
| Redis isn't answering / The database isn't answering | For 5 minutes |
| Documents have waited 30 minutes for an engine worker | Every place has been in use for half an hour |
| The portal is answering with errors | More than 5% of its answers for 10 minutes |
| Monitoring (Prometheus) isn't answering | Prometheus itself hasn't answered for 10 minutes |
