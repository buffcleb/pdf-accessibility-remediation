# Documentation

> **Reviewed 22 October 2026.** [What changed](changes-2026-10-22.md): every substantive change by reader, with links.
> In the pages, each changed section starts with **Changed 22 October 2026**, **New 22 October 2026** or (in the
> evaluation documents) **Updated 22 October 2026**.
>
> **Changed 23 October 2026:** the service admin role (#564). [What changed](changes-2026-10-23.md).
>
> **Changed 26 October 2026:** subgroups (#431). [What changed](changes-2026-10-26.md).
>
> **Changed 29 October 2026:** new tags before the save (#692). [What changed](changes-2026-10-29.md).

## Using the service

- **[User guide](user-guide/README.md)**, by role:
  - [People submitting documents](user-guide/people/README.md): signing in, uploading, results, the tag manager,
    getting help, email
  - [Group admins](user-guide/group-admins/README.md): approval, pages and limits, group settings, reports
  - [Service admins](user-guide/service-admins/README.md): what's theirs across every group, and what stays with
    super admins
  - [Super admins](user-guide/super-admins/README.md): first-time setup, services and AI levels, settings, help
    requests, queues, reports and audit
  - [Glossary](user-guide/glossary.md)

## Running it

> **Changed 22 October 2026:** Operations lists every page (CI runners, the runbooks, the SIEM export and malware
> scanning were missing).

- **[Deployment](deployment/README.md):** what you're deploying, and guides for
  [local Docker or Podman](deployment/local-docker.md), [a server](deployment/server-docker.md),
  [Kubernetes](deployment/kubernetes.md), [a first Kubernetes cluster](deployment/first-cluster/README.md),
  [the reference RKE2 deployment](deployment/reference-rke2.md),
  [Cloudflare](deployment/cloudflare.md) and [AWS](deployment/aws.md)
- **[Operations](operations/README.md):** [configuration](operations/configuration.md),
  [every setting](operations/settings-reference.md), [AI models](operations/ai-models.md),
  [email and branding](operations/email-and-branding.md), [Adobe Autotag](operations/adobe-autotag.md), [backups and migrations](operations/backups-and-migrations.md),
  [monitoring and troubleshooting](operations/monitoring.md), [limits](operations/limits.md),
  [CI on your own runners](operations/ci-runners.md), [incident response](operations/incident-response.md),
  [rotating secrets](operations/secret-rotation.md), [rolling back](operations/rollback.md),
  [the audit log to a SIEM](operations/siem.md), [malware scanning](operations/malware-scanning.md)

## How it works

> **Changed 22 October 2026:** the API reference now also covers the portal's API for scripts.

- **[Architecture](architecture/README.md):** [services, AI levels and pages](architecture/services-and-quotas.md),
  [the engine pipeline](architecture/engine-pipeline.md), [verification](architecture/verification.md),
  [the Adobe route](architecture/adobe-route.md), [the tag editor](architecture/tag-editor.md),
  [security and privacy](architecture/security-and-privacy.md), [design decisions](architecture/design-decisions.md),
  [source map](architecture/source-map.md)
- **[API reference](api/README.md):** the engine's API, for integrations and testing, and the portal's API for
  scripts with a personal API token

## Results and history

- **[Evaluation](evaluation/README.md):** how it was tested, results, and the cost model;
  [test reports](evaluation/test-reports/README.md) for individual experiments
- **[Design notes](design/README.md):** the [portal specification](design/portal-spec.md) and the
  [build log](design/build-log.md)

## For developers

- [Tests](../tests/README.md): the engine and portal test suites
- [The harness](../harness/README.md): batch testing against folders of PDFs
- [Kubernetes manifests and scripts](../deploy/k8s/README.md)
