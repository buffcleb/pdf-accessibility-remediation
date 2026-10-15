# Documentation

## Using the service

- **[User guide](user-guide/README.md)**, by role:
  - [People submitting documents](user-guide/people/README.md): signing in, uploading, results, the tag manager,
    getting help, email
  - [Group admins](user-guide/group-admins/README.md): approval, pages and limits, group settings, reports
  - [Super admins](user-guide/super-admins/README.md): first-time setup, services and AI levels, settings, help
    requests, queues, reports and audit
  - [Glossary](user-guide/glossary.md)

## Running it

- **[Deployment](deployment/README.md):** what you're deploying, and guides for
  [local Docker or Podman](deployment/local-docker.md), [a server](deployment/server-docker.md),
  [Kubernetes](deployment/kubernetes.md), [the reference RKE2 deployment](deployment/reference-rke2.md),
  [Cloudflare](deployment/cloudflare.md) and [AWS](deployment/aws.md)
- **[Operations](operations/README.md):** [configuration](operations/configuration.md),
  [AI models](operations/ai-models.md), [email and branding](operations/email-and-branding.md),
  [Adobe Autotag](operations/adobe-autotag.md), [backups and migrations](operations/backups-and-migrations.md),
  [monitoring and troubleshooting](operations/monitoring.md), [limits](operations/limits.md)

## How it works

- **[Architecture](architecture/README.md):** [services, AI levels and pages](architecture/services-and-quotas.md),
  [the engine pipeline](architecture/engine-pipeline.md), [verification](architecture/verification.md),
  [the Adobe route](architecture/adobe-route.md), [the tag editor](architecture/tag-editor.md),
  [security and privacy](architecture/security-and-privacy.md), [design decisions](architecture/design-decisions.md),
  [source map](architecture/source-map.md)
- **[API reference](api/README.md):** the engine's API, for integrations and testing

## Results and history

- **[Evaluation](evaluation/README.md):** how it was tested, results, and the cost model;
  [test reports](evaluation/test-reports/README.md) for individual experiments
- **[Design notes](design/README.md):** the [portal specification](design/portal-spec.md) and the
  [build log](design/build-log.md)

## For developers

- [Tests](../tests/README.md): the engine and portal test suites
- [The harness](../harness/README.md): batch testing against folders of PDFs
- [Kubernetes manifests and scripts](../deploy/k8s/README.md)
