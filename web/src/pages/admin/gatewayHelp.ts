/** How a super admin reaches the AI gateway's admin UI when it has no address of its own (the Kubernetes
 * deployment keeps it inside the cluster). Kept beside the guide's wording by web/tests/gatewayHelp.test.ts. */
export const GATEWAY_HELP = {
  command: "kubectl -n NAMESPACE port-forward svc/litellm 4000",
  address: "http://localhost:4000/ui",
  /** When port 4000 is taken on the admin's machine */
  otherPort: "port-forward svc/litellm 4001:4000",
  guidePath: "docs/operations/monitoring.md",
  guideHeading: "On the cluster",
  guideUrl: "https://github.com/buffcleb/pdf-accessibility-remediation/blob/main/docs/operations/monitoring.md#on-the-cluster",
};
