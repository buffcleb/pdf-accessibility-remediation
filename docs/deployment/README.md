# Deployment

The same container images run everywhere; only the settings change. Pick the guide that matches where you want to run
it.

| Guide | For | Status |
|---|---|---|
| [Local, with Docker or Podman](local-docker.md) | Trying it out, development, a demo on one machine | **Tested** with Docker (Podman: not yet tested) |
| [A server, with Docker or Podman](server-docker.md) | A single server for a team, behind your own HTTPS | Not yet tested end to end |
| [A first Kubernetes cluster](first-cluster/README.md) | A campus new to Kubernetes: what the network, OS and middleware teams each do, from bare servers to a production-ready service | A guide; the steps are those of the tested reference cluster |
| [Kubernetes](kubernetes.md) | Any Kubernetes cluster, with the manifests and scripts in `deploy/k8s` | Tested on the reference cluster below |
| [Reference deployment: an on-premises RKE2 cluster](reference-rke2.md) | How the proof of concept runs: RKE2, NAS storage, a Cloudflare Tunnel | **Tested** |
| [Cloudflare](cloudflare.md) | A Cloudflare Tunnel in front, the web app on a Cloudflare Worker, and what running more on Cloudflare would take | Tunnel **tested**; Worker tested locally; the rest is a design |
| [AWS](aws.md) | ECS on Fargate, RDS, ElastiCache, S3, EFS and SES | Not yet tested end to end |

"Not yet tested" guides are complete and careful, but haven't been run from start to finish; expect to adjust details.

## What you're deploying

> **Changed 22 October 2026:** the optional pieces are listed.

| Part | Image | Needs |
|---|---|---|
| **Web app** | `pdfrem-web` (`Dockerfile.web`): nginx with the React app, proxying `/api/` to the portal API | Nothing else |
| **Portal API** and **portal worker** | `pdfrem-portal` (`Dockerfile.portal`) | PostgreSQL, Redis, S3-compatible storage, email, the engine |
| **Engine API**, **workers** (standard and Adobe) and **scheduler** | `pdfrem-engine` (`Dockerfile`): Python, Java + veraPDF, Tesseract, fonts, Pango | Redis, a **shared job folder** seen by all of them, the AI gateway |
| **AI gateway** | `ghcr.io/berriai/litellm` with `config/litellm.yaml` | Its own PostgreSQL database, and your model servers or providers |
| **Supporting services** | PostgreSQL 16, Redis 7, an S3-compatible store (SeaweedFS in the bundled setups) | Persistent storage |
| **Optional** | The music and formula readers (`pdfrem-music`, `pdfrem-formula`), ClamAV for malware scanning, an egress proxy (Squid), Prometheus and Grafana | On Kubernetes, each is a setting in `deploy.env` ([the manifests](../../deploy/k8s/README.md)) |

Only the **web app** needs to be reachable by people. The portal API is reached through the web app's `/api/` proxy;
the engine, gateway and databases stay internal.

## Requirements

- **Storage.** PostgreSQL, Redis and the object store need ordinary block storage. The engine's job folder must be
  **shared** by its API and every worker: one volume on a single host, or a ReadWriteMany volume (NFS, EFS, CephFS…)
  across hosts.
- **AI models.** A model server with vision models for local AI (LocalAI, vLLM or Ollama, ideally on a GPU), or
  accounts with cloud providers, behind the gateway. The service also runs with no AI at all, with layout rules only.
- **Email** for sign-in codes: an SMTP relay, Amazon SES, Plunk or Cloudflare Email Service.
- **HTTPS** in front of the web app, for anything beyond a laptop.
- **Sizing** (a starting point): the engine worker uses up to a few GB of memory on large documents; everything else
  together needs about 2–3 GB. Each worker process handles one document at a time; add workers for throughput.

## Before going live

- Set strong, unique secrets: the portal's signing keys, database passwords, the S3 keys, the gateway's master key.
- Set the engine's `SERVICE_API_KEY` and the portal's `PORTAL_ENGINE_API_KEY` to the same value. The engine's API
  won't start without it (`ALLOW_NO_API_KEY=true` is for local development only).
- Give the engine a gateway key of its own, limited to the models it uses, not the master key (on Kubernetes,
  `deploy.sh` does this).
- Configure email and its domain authentication (SPF, DKIM, DMARC): see
  [Operations → Email](../operations/email-and-branding.md#email).
- Schedule backups: see [Operations → Backups](../operations/backups-and-migrations.md).
- Keep the AI gateway's admin UI off the internet (behind a VPN or SSO).
