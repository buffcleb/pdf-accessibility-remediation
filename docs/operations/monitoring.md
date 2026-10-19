# Monitoring and troubleshooting

[Operations](README.md) › Monitoring and troubleshooting

## Health checks

```bash
curl http://localhost:8000/api/v1/health
```

The engine reports Redis, veraPDF (and the veraPDF service when one is configured), the AI providers and Adobe's
status. `GET /api/v1/capacity` says how many workers are running and how many documents they process at once. `GET /api/v1/ai/models` lists the gateway's
models. The portal has its own `GET /api/v1/health` (it reports whether the database is reachable).

In the portal, **Attention** and **Queues** (super admins) show what's waiting, failing or paused.

### Alerts

The portal checks itself about once a minute and emails every super admin when something is wrong, with a reminder
while it lasts and a note when it clears; **Attention** shows the same. The times are in **Settings › Alerts**
([settings](../user-guide/super-admins/settings.md)):

| Alert | When |
|---|---|
| The document queue isn't moving | Documents are waiting (not on purpose) and none has started or finished for 60 minutes |
| The portal's background worker has stopped working | It hasn't completed a pass for 15 minutes |
| The engine has no workers | The engine reports none, or can't be reached, for 10 minutes |
| The nightly backup hasn't finished | None has for 26 hours; only with `BACKUPS=cronjob` ([backups](backups-and-migrations.md#nightly-backups-on-the-cluster)) |
| Monitoring (Prometheus) isn't answering | With [monitoring](#prometheus-and-grafana) on: Prometheus hasn't answered for 10 minutes |

With monitoring on, Prometheus's own alerts (a pod crashing, a volume filling, memory near a limit and so on) come
the same way, each with what to do; [the super admins' guide](../user-guide/super-admins/system.md#alerts) lists
them.

When the portal itself is down it can't email anyone, so point an outside uptime check at
**`https://APP_HOST/api/v1/status`**. It needs no sign-in and answers **200** with `{"status": "ok"}`, or **503**
with the problems' names while there's one (or the database can't be reached). With
[UptimeRobot](https://uptimerobot.com) (free): add an HTTP(s) monitor for that address every 5 minutes; it alerts on
anything but a 2xx answer. `/api/v1/health` stays what the readiness probe uses: it doesn't fail for a stuck queue,
which would take the portal out of service.

## Prometheus and Grafana

On the cluster, `MONITORING=on` in `deploy/k8s/deploy.env` (the default) adds monitoring to the deployment
(`deploy/k8s/manifests/95-monitoring.yaml`, #397):

- **Prometheus** reads the service's metrics every 30 seconds and keeps `PROMETHEUS_RETENTION` of them (15 days by
  default; also cut to 85% of its volume, `PROMETHEUS_SIZE`, 20 GiB). Its alerts go to super admins through the
  portal, with the portal's own ([above](#alerts)): there's no Alertmanager to set up.
- **Administration → System** (super admins) shows the main figures and charts from it, and every workload's
  pods: working, idle, starting or failing, and the room each has to scale
  ([guide](../user-guide/super-admins/system.md)). The portal reads all of it from Prometheus, so it has no access
  to the Kubernetes API itself.
- **Grafana** at `https://APP_HOST/grafana/`, for super admins only, opened from the System page. There are no
  Grafana logins: the web app's nginx asks the portal about every request, and passes the super admin's email to
  Grafana as the user (its auth proxy). It comes with a **PDF Remediation** dashboard
  (`deploy/k8s/monitoring/dashboards/`). With the Cloudflare Worker front end (`FRONTEND=worker`) there's no web
  pod, so no Grafana; Prometheus, its alerts and the System page are the same.

`MONITORING=off` removes them on the next deploy and keeps their volumes (`prometheus-data`, `grafana-data`).
To delete those too, once monitoring is off:

```bash
kubectl -n NS delete pvc prometheus-data grafana-data
```

### What's read

| From | What |
|---|---|
| portal-api, `/metrics` (port 8080, outside `/api/`, so not reachable from outside) | Requests by route and answer, and their times; documents by status, the oldest waiting, documents finished, pages charged, the worker's last pass, open alerts, queued email |
| portal-worker, port 9464 | Whether it's in a pass, passes by outcome and their time |
| engine-api, `/api/v1/metrics` | What's waiting in Redis: the engine's queue, Adobe's, the music reader's pages; whether Redis answers |
| engine-worker, engine-adobe-worker, port 9464 | Documents each is processing, of how many it can take |
| music-reader, port 9464 | Whether it's reading, pictures read and not read |
| formula-reader, port 9464 | The same, for the formula reader (#403) |
| kube-state-metrics | This namespace's pods (ready, restarts, why waiting, their `app` label), deployments, autoscalers and volumes, as the cluster sees them |
| Redis and Postgres exporters | Redis's memory, clients and lists; Postgres's connections, transactions and size (through an account that reads no table, `deploy/k8s/monitoring-role.sql`) |
| Each node's kubelet | CPU, memory and network of this namespace's containers, its volumes' use, and each liveness and readiness probe's results; everything else on the nodes is dropped as it's read |

A pod is read when its template asks (`prometheus.io/scrape`, `prometheus.io/port`, `prometheus.io/path`
annotations). The network policies let Prometheus reach those ports, the exporters reach Postgres and Redis,
the portal and Grafana reach Prometheus, and only the web pod reach Grafana (which is why nobody can name
themselves to it). Nothing in it is reachable from outside but Grafana, through the web app.

Prometheus needs to list the nodes and read their kubelets' metrics: the deployment makes a ClusterRole for that
(`NS-prometheus`), the only thing it adds outside its namespace.

### The rules

The alert rules are `deploy/k8s/monitoring/rules.yml`; the portal titles each and says what to do
(`PROM_TITLES` and `PROM_FIXES` in `portal/alerts.py`; a test keeps the names in step). A rule added there and not in
the portal still alerts, under its own name. The rules have tests:

```bash
docker run --rm --entrypoint promtool -v "$PWD/deploy/k8s/monitoring:/m:ro" -w /m prom/prometheus:v3.15.0 test rules rules_test.yml
```

### Settings

| Setting (`deploy.env`) | Default | |
|---|---|---|
| `MONITORING` | `on` | `off` removes it all (volumes kept) |
| `PROMETHEUS_RETENTION` | `15d` | How much history is kept: `15d`, `8w`, `1y` |
| `PROMETHEUS_SIZE` | `20Gi` | Prometheus's volume; history is also cut to 85% of it (or `PROMETHEUS_RETENTION_SIZE`, such as `17GB`) |
| `PROMETHEUS_MEMORY_LIMIT` | `2Gi` | |
| `GRAFANA_SIZE` | `1Gi` | Grafana's volume: its users and the dashboards made in it |
| `MONITORING_STORAGE_CLASS` | `STORAGE_CLASS` | For both volumes |

The images are pinned by digest in `scripts/deploy.sh` (`PROMETHEUS_IMAGE`, `GRAFANA_IMAGE`, `KSM_IMAGE`,
`REDIS_EXPORTER_IMAGE`, `POSTGRES_EXPORTER_IMAGE`; each can be set in `deploy.env`). Its passwords
(`MONITORING_DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_SECRET_KEY`) are made in `deploy/k8s/portal.env` on the
first deploy and go only to the `pdfrem-monitoring-env` secret. Nobody signs in with Grafana's admin password (its
login form and basic sign-in are off); Grafana just needs one set. Port-forwarding goes around the network policies,
so someone with `kubectl` access to the namespace can reach Grafana directly and name any user; that access is
already everything.

A pod that doesn't seem to be read: Grafana's **Explore**, or `kubectl -n NS port-forward svc/prometheus 9090`
and http://localhost:9090/targets, shows every target and the last error reading it.

### Liveness: what restarts a stuck pod

On the cluster each long-running pod has a liveness probe, so one that stops working is restarted rather than
looking healthy:

| Pod | Checked | Restarted after |
|---|---|---|
| engine-worker, engine-adobe-worker | `/tmp/worker.alive`, touched every 30 s by the worker's own loop while it's connected to Redis and taking tasks | about 6 minutes without a touch (stale for 3 minutes, three checks a minute apart) |
| portal-worker | `/tmp/portal-worker.alive`, touched after every pass that completes | about 12 minutes without a completed pass |
| engine-api, portal-api | `GET /api/v1/live`: the process answers (nothing else is checked) | a minute of no answer |
| web | `GET /` | a minute of no answer |
| verapdf | `GET /api/info` | about two minutes of no answer (after a minute's start) |
| prometheus, grafana | their own health pages | about two minutes of no answer |

A long document doesn't stop a worker's touches: tasks run in child processes beside the loop. A document cut off by
a restart is handed out again (up to two starts in all), and the portal shows that its worker was lost meanwhile. A
portal worker whose every pass fails (as on 16 October, when one result couldn't be saved) is restarted over and
over: `kubectl get pods` shows the restarts, and its log says why.

The readiness probes still use `/api/v1/health`, which checks the database (portal) or Redis and veraPDF (engine):
a pod that can't reach them is taken out of service without being restarted.

Apart from cloudflared (two copies) and what scales with the queue, every component runs as one copy. Draining or
powering down a node interrupts the service for a few minutes while its pods start elsewhere; documents in progress
there are started again. Replicas and disruption budgets are for production.

## Logs

- Each job logs its stages and AI calls. With the `debug` flag, the job folder keeps `analysis.json` and
  `ai_log.jsonl` (every prompt and reply, so the document's own text with them) and a traceback in `error.json`,
  on the shared job folder until the job's `JOB_TTL_HOURS` clears it like everything else. The flag is the
  portal's to set; use it on a document whose text may stay there that long.
- The AI gateway's admin UI → Logs shows every AI call, with errors.
- The portal worker logs what it sends to the engine, and any email that fails to send (with its retries). Every
  email, sent or not, is also in **Reports → Activity → Email** ([the email log](email-and-branding.md#the-email-log)).

## On the cluster

The commands you need on a Kubernetes deployment, each with what it shows. `NS` is the namespace from
`deploy/k8s/deploy.env` (`pdf-remediation` in the reference deployment); the `KUBECONFIG` is the one `deploy.env`
names. None of these changes anything.

```bash
scripts/deploy.sh status
```

Every pod with its readiness, restarts and node, the volumes, and the Ingress. The first thing to run when something
seems wrong: a pod that isn't `1/1 Running`, or a restart count that keeps climbing, is the place to look.

```bash
kubectl -n NS logs deploy/engine-worker --since=30m
```

A pod's recent output; `-f` follows it, and `deploy/portal-worker`, `deploy/portal-api`, `deploy/engine-api`,
`statefulset/redis`, `deploy/litellm` name the others. Jobs stuck *processing* are explained by the engine worker's
log (a document whose worker was lost shows as "Worker lost: waiting to be retried" on Queues and Attention); the portal worker's log says what it sent to the engine and which emails failed.

```bash
kubectl -n NS describe pod POD
```

Why a pod isn't starting or keeps restarting: the events at the end (an image that can't be pulled, a volume that
won't mount, a container killed for memory, a probe that fails). `kubectl -n NS get events --sort-by=.lastTimestamp`
lists the namespace's recent events together.

```bash
kubectl -n NS top pods
```

Each pod's CPU and memory now (needs metrics-server). A worker near its memory limit on a long document is what
`WORKER_MEMORY_LIMIT` in `deploy.env` is for.

```bash
kubectl -n NS get scaledobject,hpa
```

The autoscalers: how many workers are wanted and running, and the queue count KEDA read. See
[scaling with the queue](../../deploy/k8s/README.md#scaling-with-the-queue).

```bash
kubectl -n NS get networkpolicy
```

Who may talk to what. The policies are listed in [the deploy README](../../deploy/k8s/README.md#network-policies-and-the-redis-password);
ten with the tunnel front end, eleven with the Worker front end, and three more with monitoring.

```bash
kubectl -n NS port-forward svc/litellm 4000
```

The AI gateway's admin UI on http://localhost:4000/ui while the command runs: every AI call, its cost and any error.
This is the only way in: the network policies let the engine and the portal reach the gateway and nothing else, so
an address of its own through a tunnel or an Ingress answers with the front end's error page. If port 4000 is
taken on your machine (a local stack's gateway, for one), put another before it, `port-forward svc/litellm 4001:4000`,
and open http://localhost:4001/ui. A sign-in to the UI lasts a day; "Authentication Error - Expired Key" there
means sign in again, not that the engine's key has run out.

```bash
kubectl -n NS rollout restart deploy/engine-worker
```

The one of these that changes something: restarts a deployment's pods one by one (a stuck worker, a setting that
needs a fresh start). A running document is finished first (the worker has a grace period). For everything else,
`scripts/deploy.sh` applies the manifests and rolls what changed.

## Capacity and timing (measured)

| Setup | 109-page dissertation (figure-heavy) | Typical 2–20 page document |
|---|---|---|
| LocalAI on an RTX 3090 + RTX 4060 Ti | about 350 s | 5–20 s |
| Ollama on an Apple M1 Max | about 1,120 s | 10–40 s |

Most time on large documents goes to AI review calls, then veraPDF validation, which runs several times per job
(through the [veraPDF service](configuration.md#verapdf-service) when it's running). Add workers to process documents
in parallel; each worker process handles one document at a time. Memory: a worker can use a few GB on large documents
(the PDF in memory, page renders, and Java when veraPDF runs from the command line).

## Common problems

| Symptom | Check |
|---|---|
| Jobs stay *processing* | Is the engine worker running? Is Redis healthy? Check the worker's logs |
| Jobs stay *Waiting* in the portal | A daily page limit may be reached, or the queue paused (the document's page says so); otherwise check the portal worker's logs and that it can reach the engine |
| `ai_provider_unavailable` | The gateway (or AI host) must be reachable from the worker, and the model name must match exactly (the error lists what's available) |
| "every AI call failed" / "AI review did not run" | The gateway's admin UI → Logs shows each failed call. Common causes: the model server couldn't load the model (out of GPU memory, or LocalAI's "load is in cooldown after a recent failure"), or a thinking model returned empty answers |
| The gateway refuses calls with a budget error | `max_budget` or a virtual key's budget is spent: raise it in `config/litellm.yaml` or the admin UI |
| "failed to process mtmd chunk" / "Context size has been exceeded" | The image or prompt is too large for the model's context; the engine retries smaller; raise `context_size` if it persists |
| `tag_integrity_failed` | An engine bug: keep the input file and the job's `error.json`, and report them |
| Sign-in codes don't arrive | Settings → Email → Send test email; the portal's logs; [the break-glass code](email-and-branding.md#when-sign-in-codes-dont-arrive) |
| Changes to settings have no effect | Containers keep the settings they started with: recreate them (`docker compose up -d`), or rerun `scripts/deploy.sh` |
