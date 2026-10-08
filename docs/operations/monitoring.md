# Monitoring and troubleshooting

[Operations](README.md) › Monitoring and troubleshooting

## Health checks

```bash
curl http://localhost:8000/api/v1/health
```

The engine reports Redis, veraPDF, the AI providers and Adobe's status. `GET /api/v1/ai/models` lists the gateway's
models. The portal has its own `GET /api/v1/health` (it reports whether the database is reachable).

In the portal, **Attention** and **Queues** (super admins) show what's waiting, failing or paused.

## Logs

- Each job logs its stages and AI calls. With the `debug` flag, the job folder keeps `analysis.json` and
  `ai_log.jsonl` (every prompt and reply).
- The AI gateway's admin UI → Logs shows every AI call, with errors.
- The portal worker logs what it sends to the engine, and any email that fails to send (with its retries).

## Capacity and timing (measured)

| Setup | 109-page dissertation (figure-heavy) | Typical 2–20 page document |
|---|---|---|
| LocalAI on an RTX 3090 + RTX 4060 Ti | about 350 s | 5–20 s |
| Ollama on an Apple M1 Max | about 1,120 s | 10–40 s |

Most time on large documents goes to AI review calls, then veraPDF validation, which runs several times per job. Add
workers to process documents in parallel; each worker process handles one document at a time. Memory: a worker can use
a few GB on large documents (Java for veraPDF, the PDF in memory, page renders).

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
