# Local, with Docker or Podman

[Deployment](README.md) › Local, with Docker or Podman

Two Compose stacks run side by side on one machine: the **engine** (`docker-compose.yml`) and the **portal**
(`docker-compose.portal.yml`). This is how the service was developed and tested.

> [!NOTE]
> Tested with Docker Desktop on macOS (Apple silicon). Podman is covered below but **not yet tested**.

## What runs

**Engine stack** (`docker-compose.yml`):

| Service | Role |
|---|---|
| `api` | The engine API, on port 8000 |
| `worker` | Remediation workers (`WORKER_CONCURRENCY`, 2 by default) |
| `adobe-worker` | Adobe Autotag jobs only; idle unless `ADOBE_ENABLED=true` |
| `beat` | The scheduler that clears old job folders (exactly one) |
| `redis` | The job queue |
| `verapdf` | veraPDF kept running for the PDF/UA checks (amd64 only, emulated on Apple silicon) |
| `litellm`, `litellm-db` | The AI gateway (admin UI on `127.0.0.1:4000/ui`) and its database |
| `music-reader` | Optional (profile `music`, with `OMR_ENABLED=true` in `.env`): reads the notes of music examples |
| `fake-adobe` | Testing only (profile `fake-adobe`): a stand-in for Adobe |

> **Changed 22 October 2026:** malware scanning and the egress proxy are named among the Kubernetes options.

The formula reader (#403) isn't in the Compose files; it's a Kubernetes option (`FORMULA_READER`). So are malware
scanning with ClamAV (`MALWARE_SCAN`) and the egress proxy (`EGRESS_PROXY`).

**Portal stack** (`docker-compose.portal.yml`):

| Service | Role |
|---|---|
| `portal-web` | The web app, on port 8088 |
| `portal-api` | The portal API (applies database migrations on start) |
| `portal-worker` | Sends documents to the engine, collects results, settles pages, sends email, applies retention |
| `portal-db`, `portal-redis` | PostgreSQL 16 and Redis |
| `seaweedfs` | S3-compatible storage, encrypted at rest |
| `mailpit` | Catches email: every message, including sign-in codes, at http://localhost:8025 |

## Steps

1. **Engine settings.** Copy the example and fill in the gateway section (`LITELLM_*`, `LOCAL_AI_*`, `GATEWAY_*`).
   With no model server yet, leave the AI out: jobs then use layout rules only.

   ```bash
   cp .env.example .env
   ```

2. **Start the engine.**

   ```bash
   docker compose up -d --build
   ```

   ```bash
   curl localhost:8000/api/v1/health
   ```

3. **Portal settings.** Copy the example, then fill in your email address as the first super admin, the passwords and
   `PORTAL_S3_SSE_KEK` (`openssl rand -hex 32`).

   ```bash
   cp .env.portal.example .env.portal
   ```

   Then generate the signing keys and paste the two lines it prints into `.env.portal`:

   ```bash
   docker compose -p portal -f docker-compose.portal.yml --env-file .env.portal run --rm --no-deps portal-api python -m portal.cli genkeys
   ```

4. **Start the portal.**

   ```bash
   docker compose -p portal -f docker-compose.portal.yml --env-file .env.portal up -d --build
   ```

5. **Sign in** at http://localhost:8088 with the address you put in `PORTAL_BOOTSTRAP_SUPER_ADMINS`. The code arrives
   in Mailpit at http://localhost:8025. Then follow [First-time setup](../user-guide/super-admins/first-time-setup.md).

After any code or settings change, rebuild and recreate the containers with the same `up -d --build` commands:
containers keep the image and settings they started with.

## AI models

The gateway's models are in `config/litellm.yaml`. For local AI, point `LOCAL_AI_BASE_URL` at a LocalAI, vLLM or Ollama
server with vision models (see [Operations → AI models](../operations/ai-models.md)). From inside the containers, a
model server on the same machine is at `host.docker.internal`.

## Trying it without the portal

```bash
python harness/make_samples.py samples
```

```bash
python harness/run_harness.py samples --provider mock --expect-compliant
```

See [harness/README.md](../../harness/README.md).

## Podman

> [!WARNING]
> Not yet tested.

The Compose files use nothing Docker-specific beyond `host.docker.internal`, so they should run with Podman 4.7 or
later and its `podman compose` command (which uses `docker-compose` or `podman-compose`):

```bash
podman compose up -d --build
```

```bash
podman compose -p portal -f docker-compose.portal.yml --env-file .env.portal up -d --build
```

Differences to expect:

- **The host's address** is `host.containers.internal` under Podman. Set `PORTAL_ENGINE_URL` (and any model server
  address) to use it, or rely on Podman's `host.docker.internal` alias where your version provides it.
- **Rootless Podman** can't bind ports below 1024; the defaults (8000, 8088, 8025) are fine.
- **SELinux** hosts: add `:Z` to bind mounts if the containers can't read `config/litellm.yaml`.
- On macOS and Windows, give the Podman machine at least 8 GB of memory.
