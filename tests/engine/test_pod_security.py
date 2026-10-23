"""#466: every pod the deployment makes meets Kubernetes's "baseline" Pod Security Standard, which the namespace enforces
(00-namespace.yaml), so a privileged pod there is refused. "restricted" is warned and audited; the pods that don't meet
it, and why, are listed in RESTRICTED_EXCEPTIONS (deploy/k8s/README.md says the same).
https://kubernetes.io/docs/concepts/security/pod-security-standards/"""
from __future__ import annotations

from tests.engine.test_manifest_policies import _docs

BASELINE_CAPS = {"AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD", "NET_BIND_SERVICE",
                 "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"}
SAFE_SYSCTLS = {"kernel.shm_rmid_forced", "net.ipv4.ip_local_port_range", "net.ipv4.ip_unprivileged_port_start",
                "net.ipv4.tcp_syncookies", "net.ipv4.ping_group_range", "net.ipv4.ip_local_reserved_ports",
                "net.ipv4.tcp_keepalive_time", "net.ipv4.tcp_fin_timeout", "net.ipv4.tcp_keepalive_intvl",
                "net.ipv4.tcp_keepalive_probes"}
SELINUX_TYPES = {None, "", "container_t", "container_init_t", "container_kvm_t", "container_engine_t"}
RESTRICTED_VOLUMES = {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "persistentVolumeClaim", "projected",
                      "secret"}

# Pods that meet "baseline" but not "restricted", and why (warned and audited, not refused)
RESTRICTED_EXCEPTIONS = {
    # Starts as root to make the data directory postgres's own (mode 0700), with only the five capabilities that
    # step needs, then drops to postgres (20-postgres.yaml)
    "StatefulSet/postgres": "starts as root, with CHOWN, DAC_OVERRIDE, FOWNER, SETGID and SETUID only",
    # The gateway's image runs as root; running it as another user hasn't been tried. No capabilities, no escalation
    "Deployment/litellm": "the LiteLLM image runs as root (all capabilities dropped)",
}


def _templates():
    """Every pod template: workload name -> pod spec."""
    for _, doc in _docs():
        kind = doc["kind"]
        if kind in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
            yield f"{kind}/{doc['metadata']['name']}", doc["spec"]["template"]["spec"]
        elif kind == "CronJob":
            yield f"{kind}/{doc['metadata']['name']}", doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def _containers(spec):
    return spec.get("initContainers", []) + spec.get("containers", [])


def baseline(spec) -> list[str]:
    bad = []
    for host in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(host):
            bad.append(host)
    bad += [f"hostPath volume {v['name']}" for v in spec.get("volumes", []) if "hostPath" in v]
    pod_sc = spec.get("securityContext") or {}
    bad += [f"sysctl {s['name']}" for s in pod_sc.get("sysctls", []) if s["name"] not in SAFE_SYSCTLS]
    for sc, where in [(pod_sc, "pod")] + [(c.get("securityContext") or {}, c["name"]) for c in _containers(spec)]:
        if (sc.get("seccompProfile") or {}).get("type") == "Unconfined":
            bad.append(f"{where}: seccomp Unconfined")
        if (sc.get("appArmorProfile") or {}).get("type") == "Unconfined":
            bad.append(f"{where}: AppArmor unconfined")
        se = sc.get("seLinuxOptions") or {}
        if se.get("type") not in SELINUX_TYPES or se.get("user") or se.get("role"):
            bad.append(f"{where}: SELinux options")
    for c in _containers(spec):
        sc = c.get("securityContext") or {}
        if sc.get("privileged"):
            bad.append(f"{c['name']}: privileged")
        extra = set((sc.get("capabilities") or {}).get("add") or []) - BASELINE_CAPS
        if extra:
            bad.append(f"{c['name']}: capabilities {sorted(extra)}")
        if sc.get("procMount", "Default") != "Default":
            bad.append(f"{c['name']}: procMount")
        bad += [f"{c['name']}: hostPort {p['hostPort']}" for p in c.get("ports", []) if p.get("hostPort")]
    return bad


def restricted(spec) -> list[str]:
    """What keeps a pod from "restricted", beyond baseline."""
    bad = [f"volume {v['name']} ({next(k for k in v if k != 'name')})" for v in spec.get("volumes", [])
           if not set(v) - {"name"} <= RESTRICTED_VOLUMES]
    pod_sc = spec.get("securityContext") or {}
    for c in _containers(spec):
        sc = c.get("securityContext") or {}
        if sc.get("allowPrivilegeEscalation") is not False:
            bad.append(f"{c['name']}: allowPrivilegeEscalation not false")
        if not sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot")):
            bad.append(f"{c['name']}: runAsNonRoot not set")
        if sc.get("runAsUser", pod_sc.get("runAsUser")) == 0:
            bad.append(f"{c['name']}: runs as root")
        seccomp = (sc.get("seccompProfile") or pod_sc.get("seccompProfile") or {}).get("type")
        if seccomp not in ("RuntimeDefault", "Localhost"):
            bad.append(f"{c['name']}: seccomp profile not set")
        caps = sc.get("capabilities") or {}
        if "ALL" not in (caps.get("drop") or []):
            bad.append(f"{c['name']}: capabilities not all dropped")
        if set(caps.get("add") or []) - {"NET_BIND_SERVICE"}:
            bad.append(f"{c['name']}: adds capabilities")
    return bad


def test_every_pod_meets_baseline():
    found = {name: baseline(spec) for name, spec in _templates()}
    assert len(found) > 15  # every workload was read, jobs and optional pieces included
    assert {n: b for n, b in found.items() if b} == {}


def test_the_namespace_enforces_baseline_and_warns_on_restricted():
    (ns,) = [d for _, d in _docs() if d["kind"] == "Namespace"]
    labels = ns["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "baseline"
    assert labels["pod-security.kubernetes.io/warn"] == labels["pod-security.kubernetes.io/audit"] == "restricted"


def test_the_pods_short_of_restricted_are_the_listed_ones():
    """A new pod short of "restricted" is either fixed or added to the exceptions, with its reason."""
    short = {name: restricted(spec) for name, spec in _templates()}
    assert {n for n, b in short.items() if b} == set(RESTRICTED_EXCEPTIONS)



def test_only_pods_that_use_the_kubernetes_api_get_a_token():
    """#470: a pod holds an API token only when it names a service account with a Role or ClusterRole bound to it;
    every other pod turns the token off, and so does the namespace's default account."""
    docs = [d for _, d in _docs()]
    bound = {s["name"] for d in docs if d["kind"] in ("RoleBinding", "ClusterRoleBinding")
             for s in d.get("subjects", []) if s.get("kind") == "ServiceAccount"}
    for name, spec in _templates():
        account = spec.get("serviceAccountName")
        if account:
            assert account in bound, name  # a service account of its own is for rights it uses
        else:
            assert spec.get("automountServiceAccountToken") is False, name
    (default,) = [d for d in docs if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == "default"]
    assert default["automountServiceAccountToken"] is False


def test_the_pods_that_read_uploads_can_be_kept_on_nodes_of_their_own():
    """#471: ENGINE_NODES puts the pods that open uploaded documents on nodes of their own (a node selector and a
    toleration, rendered where ${ENGINE_PLACEMENT} stands); nothing else carries it."""
    import re

    from tests.engine.test_manifest_policies import K8S

    placed = set()
    for path in sorted(K8S.glob("manifests/*.yaml")):
        for doc in path.read_text().split("\n---\n"):
            if re.search(r"(?m)^\$\{ENGINE_PLACEMENT\}$", doc):
                placed.add(re.search(r"(?m)^kind: (\w+)\nmetadata:\n  name: ([\w-]+)", doc).group(2))
    assert placed == {"engine-api", "engine-worker", "engine-adobe-worker", "engine-beat", "music-reader",
                      "formula-reader"}
