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
log; the portal worker's log says what it sent to the engine and which emails failed.

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
eight with the tunnel front end, nine with the Worker front end.

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
