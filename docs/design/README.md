# Design notes

How the portal was designed and built. These pages are a record: the [architecture](../architecture/README.md)
section describes the service as it is today.

| Page | What it is |
|---|---|
| [Portal specification](portal-spec.md) | The portal's design: goals, roles, sign-in, quotas, jobs and files, screens, data model, security requirements, deployment targets and build phases, with the decisions made along the way. Where it was built differently, each section says what was built |
| [Build log](build-log.md) | What was built, phase by phase and feature by feature, with the details that mattered: endpoints, migrations, edge cases and checks |
| [Engine refactoring plan](engine-refactor-plan.md) | A review of the remediation engine made on 24 October 2026: where it has become hard to change, and a ranked list of refactors, each with the problem, the change, the risk, how to check that no document's output changed, and its size |
