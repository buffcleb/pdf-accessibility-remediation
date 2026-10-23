"""deploy/node/rke2-drain.sh: a node drains itself before shutdown (unless another node is already out, or the cluster
can't be reached), and lets pods back on after boot only if it drained itself. Never stops a shutdown. Run against a
stand-in kubectl that records what it was asked to do."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "node" / "rke2-drain.sh"

# Answers `get nodes` / `get node NAME` from a JSON list of (name, status); records drain and uncordon calls
FAKE = r'''
import json, os, sys
c = json.loads(os.environ["FAKE_CLUSTER"])
a = [x for x in sys.argv[1:] if not x.startswith("--request-timeout")]
if c.get("unreachable"):
    sys.exit(1)
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(a) + "\n")
if a[:2] == ["get", "nodes"]:
    if "--no-headers" not in a:
        print("NAME STATUS ROLES AGE VERSION")
    for n, s in c["nodes"]:
        print(f"{n} {s} control-plane,etcd,master 9d v1.33.13+rke2r1")
elif a[:2] == ["get", "node"]:
    print(next(f"{n} {s} control-plane 9d v1" for n, s in c["nodes"] if n == a[2]))
elif a[0] == "drain":
    sys.exit(c.get("drain_exit", 0))
'''


@pytest.fixture
def drain(tmp_path):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(f"#!{sys.executable}\n{FAKE}")
    kubectl.chmod(0o755)
    log, mark = tmp_path / "calls.log", tmp_path / "drained"

    def run(action: str, cluster: dict) -> tuple[int, str, list[str]]:
        log.write_text("")
        env = {**os.environ, "KUBECTL": str(kubectl), "NODE_NAME": "n1", "DRAIN_MARK": str(mark),
               "FAKE_CLUSTER": json.dumps(cluster), "FAKE_LOG": str(log)}
        p = subprocess.run(["bash", str(SCRIPT), action], env=env, capture_output=True, text=True, timeout=30)
        calls = [line.split()[0] for line in log.read_text().splitlines() if line]
        return p.returncode, p.stdout, calls

    return run, mark


UP = {"nodes": [["n1", "Ready"], ["n2", "Ready"], ["n3", "Ready"]]}


def test_a_node_drains_itself_when_the_others_are_up(drain):
    run, mark = drain
    code, out, calls = run("down", UP)
    assert code == 0 and "drain" in calls and mark.exists() and "n1 drained" in out


def test_no_drain_when_another_node_is_out_or_the_cluster_cant_be_reached(drain):
    run, mark = drain
    for status in ("NotReady", "Ready,SchedulingDisabled"):
        code, out, calls = run("down", {"nodes": [["n1", "Ready"], ["n2", status], ["n3", "Ready"]]})
        assert code == 0 and "drain" not in calls and not mark.exists() and "another node" in out
    code, out, _ = run("down", {"unreachable": True})
    assert code == 0 and not mark.exists() and "can't be reached" in out


def test_a_drain_that_runs_out_of_time_still_lets_the_shutdown_go_ahead(drain):
    run, mark = drain
    code, out, _ = run("down", {**UP, "drain_exit": 1})
    assert code == 0 and mark.exists() and "shutting down anyway" in out


def test_after_boot_it_uncordons_only_a_node_it_drained(drain):
    run, mark = drain
    code, _, calls = run("up", UP)
    assert code == 0 and "uncordon" not in calls  # cordoned by someone else, or never drained: left alone
    mark.touch()
    code, out, calls = run("up", UP)
    assert code == 0 and "uncordon" in calls and not mark.exists() and "uncordoned" in out
