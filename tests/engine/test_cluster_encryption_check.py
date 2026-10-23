"""#452: scripts/check_cluster_encryption.sh tells whether traffic between the cluster's nodes is encrypted, from what
the cluster reports. Run against a stand-in kubectl that answers as each kind of cluster would."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_cluster_encryption.sh"

# A kubectl that answers from a JSON description of a cluster: its nodes (name -> the backend each reports), its
# configmaps in kube-system (name -> data), and Calico's Felix setting. Anything else is "not found".
FAKE = r'''
import json, os, sys
c = json.loads(os.environ["FAKE_CLUSTER"])
a = [x for x in sys.argv[1:] if not x.startswith("--request-timeout")]
path = next((x.split("=", 1)[1] for x in a if x.startswith("jsonpath=")), None)
if "-o" in a:
    path = a[a.index("-o") + 1].split("=", 1)[1]
if c.get("unreachable"):
    sys.exit(1)
if a[:2] == ["get", "nodes"] or a[2:4] == ["get", "nodes"]:
    if path is None:
        sys.exit(0)
    if "annotations" in path:
        print("".join(f"{n}={b}\n" for n, b in c["nodes"].items()), end="")
    else:
        print("".join(f"{n}\n" for n in c["nodes"]), end="")
    sys.exit(0)
if "configmap" in a:
    data = c.get("configmaps", {}).get(a[a.index("configmap") + 1])
    if data is None:
        sys.exit(1)
    if path:
        key = path.strip("{}").split("data.", 1)[1].replace("\\.", ".")
        print(data.get(key, ""), end="")
    sys.exit(0)
if "felixconfiguration" in a:
    if "felix" not in c:
        sys.exit(1)
    print(c["felix"], end="")
    sys.exit(0)
sys.exit(1)
'''


@pytest.fixture
def check(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(f"#!{sys.executable}\n{FAKE}")
    kubectl.chmod(0o755)

    def run(cluster: dict) -> tuple[int, str]:
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_CLUSTER": json.dumps(cluster)}
        p = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout.strip().splitlines()[-1]

    return run


def _canal(setting: str, nodes: dict) -> dict:
    conf = json.dumps({"Network": "10.42.0.0/16", "Backend": {"Type": setting}}, indent=2)
    return {"nodes": nodes, "configmaps": {"rke2-canal-config": {"net-conf.json": conf}}}


THREE = ("n1", "n2", "n3")


def test_canal_with_wireguard_on_every_node_is_encrypted(check):
    code, line = check(_canal("wireguard", {n: "wireguard" for n in THREE}))
    assert code == 0 and line == "encrypted: WireGuard on 3 of 3 nodes"


def test_wireguard_set_but_the_plugin_not_restarted_says_so(check):
    code, line = check(_canal("wireguard", {"n1": "wireguard", "n2": "vxlan", "n3": "vxlan"}))
    assert code == 1 and "only 1 of 3 nodes" in line and "rollout restart" in line


def test_canal_with_vxlan_is_not_encrypted(check):
    code, line = check(_canal("vxlan", {n: "vxlan" for n in THREE}))
    assert code == 1 and line.startswith("not encrypted") and "vxlan" in line


def test_cilium_and_calico(check):
    nodes = {n: "" for n in THREE}
    assert check({"nodes": nodes, "configmaps": {"cilium-config": {"enable-wireguard": "true"}}})[0] == 0
    assert check({"nodes": nodes, "configmaps": {"cilium-config": {"enable-ipsec": "true"}}})[0] == 0
    assert check({"nodes": nodes, "configmaps": {"cilium-config": {"enable-wireguard": "false"}}})[0] == 1
    assert check({"nodes": nodes, "felix": "true"})[0] == 0
    assert check({"nodes": nodes, "felix": ""})[0] == 1


def test_one_node_needs_nothing_and_an_unknown_plugin_or_cluster_cant_be_told(check):
    assert check({"nodes": {"solo": ""}})[0] == 0
    code, line = check({"nodes": {n: "" for n in THREE}})
    assert code == 2 and line.startswith("can't tell")
    assert check({"unreachable": True, "nodes": {}})[0] == 2
