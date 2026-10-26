"""#592: a volume grows when its size in deploy.env goes up. deploy/k8s/volume_sizes.py decides from the cluster's
claims, StatefulSets and storage classes (here, fake kubectl output); scripts/deploy.sh shows the plan, patches the
claims and recreates the StatefulSets around their pods, run here against a fake kubectl in a copy of the deploy
files (as the ingress tests do): never against a cluster."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.engine.test_ingress_controllers import ROOT

_spec = importlib.util.spec_from_file_location("volume_sizes", ROOT / "deploy" / "k8s" / "volume_sizes.py")
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)


def pvc(name: str, size: str, cls: str = "block", capacity: str | None = None, conditions: list | None = None) -> dict:
    status: dict = {"capacity": {"storage": capacity or size}}
    if conditions:
        status["conditions"] = conditions
    return {"metadata": {"name": name},
            "spec": {"storageClassName": cls, "resources": {"requests": {"storage": size}}}, "status": status}


def sts(name: str, size: str) -> dict:
    return {"metadata": {"name": name}, "spec": {"volumeClaimTemplates": [
        {"metadata": {"name": "data"}, "spec": {"resources": {"requests": {"storage": size}}}}]}}


CLASSES = [{"metadata": {"name": "block"}, "allowVolumeExpansion": True}, {"metadata": {"name": "nfs"}}]
LIVE = [pvc("data-seaweedfs-0", "50Gi"), pvc("data-postgres-0", "10Gi"), pvc("jobdata", "50Gi", "nfs"),
        pvc("prometheus-data", "20Gi"), pvc("backups", "20Gi", "")]
SETS = [sts("seaweedfs", "50Gi"), sts("postgres", "10Gi")]


@pytest.mark.parametrize("quantity,expected", [("50Gi", 50 * 2**30), ("10G", 10**10), ("1.5Ti", 3 * 2**39),
                                               ("1073741824", 2**30), ("512Mi", 2**29), ("100k", 10**5)])
def test_sizes_are_read_as_kubernetes_writes_them(quantity, expected):
    assert vs.size_bytes(quantity) == expected


def test_the_same_size_written_another_way_changes_nothing():
    steps, stop = vs.plan(LIVE, SETS, CLASSES, ["FILES_SIZE=51200Mi:statefulset/seaweedfs"])
    assert (steps, stop) == ([], [])


def test_a_bigger_file_store_grows_its_claim_and_recreates_its_statefulset():
    steps, stop = vs.plan(LIVE, SETS, CLASSES, ["DB_SIZE=10Gi:statefulset/postgres",
                                                "FILES_SIZE=100Gi:statefulset/seaweedfs"])
    assert stop == []
    assert steps == [["grow", "data-seaweedfs-0", "50Gi", "100Gi", "FILES_SIZE", "statefulset/seaweedfs"],
                     ["recreate", "seaweedfs", "50Gi", "100Gi", "FILES_SIZE"]]


def test_a_plain_claim_grows_without_recreating_anything():
    steps, stop = vs.plan(LIVE, SETS, CLASSES, ["PROMETHEUS_SIZE=40Gi:pvc/prometheus-data:deployment/prometheus"])
    assert (steps, stop) == ([["grow", "prometheus-data", "20Gi", "40Gi", "PROMETHEUS_SIZE", "deployment/prometheus"]],
                             [])


def test_a_claim_grown_by_hand_still_gets_its_statefulset_made_again():
    # The runbook's manual steps stopped halfway: the claim patched, the StatefulSet not yet deleted
    steps, stop = vs.plan([pvc("data-seaweedfs-0", "100Gi")], SETS, CLASSES, ["FILES_SIZE=100Gi:statefulset/seaweedfs"])
    assert (steps, stop) == ([["recreate", "seaweedfs", "50Gi", "100Gi", "FILES_SIZE"]], [])


def test_a_first_deploy_has_nothing_to_grow():
    assert vs.plan([], [], CLASSES, ["FILES_SIZE=100Gi:statefulset/seaweedfs", "JOBDATA_SIZE=50Gi:pvc/jobdata"]) == \
        ([], [])


def test_a_smaller_size_stops_the_deploy():
    steps, stop = vs.plan(LIVE, SETS, CLASSES, ["DB_SIZE=5Gi:statefulset/postgres"])
    assert steps == [] or all(s[0] == "recreate" for s in steps)
    assert len(stop) == 1 and "can't shrink" in stop[0] and "DB_SIZE=10Gi" in stop[0]


def test_a_class_that_cant_grow_volumes_stops_the_deploy_and_says_what_to_do():
    _, stop = vs.plan(LIVE, SETS, CLASSES, ["JOBDATA_SIZE=100Gi:pvc/jobdata"])
    assert len(stop) == 1
    assert "storage class nfs" in stop[0] and "allowVolumeExpansion" in stop[0] and "JOBDATA_SIZE=50Gi" in stop[0]
    assert "storage.md#when-the-class-cant-grow-a-volume" in stop[0]


def test_a_volume_made_by_hand_cant_be_grown():
    _, stop = vs.plan(LIVE, SETS, CLASSES, ["BACKUP_SIZE=40Gi:pvc/backups"])
    assert len(stop) == 1 and "no storage class" in stop[0] and "BACKUP_SIZE=20Gi" in stop[0]


@pytest.mark.parametrize("spec,words", [("DB_SIZE=:statefulset/postgres", "isn't set"),
                                        ("DB_SIZE=lots:statefulset/postgres", "isn't a size")])
def test_a_bad_setting_is_named(spec, words):
    _, stop = vs.plan(LIVE, SETS, CLASSES, [spec])
    assert len(stop) == 1 and words in stop[0]


def test_how_far_a_claim_has_grown():
    assert vs.state(pvc("x", "100Gi", capacity="100Gi"), "100Gi") == "grown"
    pending = [{"type": "FileSystemResizePending", "status": "True"}]
    assert vs.state(pvc("x", "100Gi", capacity="50Gi", conditions=pending), "100Gi") == "pending"
    assert vs.state(pvc("x", "100Gi", capacity="50Gi", conditions=[{"type": "Resizing", "status": "True"}]),
                    "100Gi") == "resizing"


def test_the_command_line_writes_the_plan_or_the_reasons(tmp_path):
    files = {}
    for name, items in (("pvcs", LIVE), ("sets", SETS), ("classes", CLASSES)):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps({"items": items}))
    script = str(ROOT / "deploy" / "k8s" / "volume_sizes.py")
    args = ["python3", script, "plan", str(files["pvcs"]), str(files["sets"]), str(files["classes"])]
    ok = subprocess.run(args + ["FILES_SIZE=100Gi:statefulset/seaweedfs"], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.splitlines()[0].split("\t")[:4] == ["grow", "data-seaweedfs-0", "50Gi",
                                                                                "100Gi"]
    bad = subprocess.run(args + ["JOBDATA_SIZE=10Gi:pvc/jobdata"], capture_output=True, text=True)
    assert bad.returncode == 1 and bad.stdout == "" and "can't shrink" in bad.stderr


# --- scripts/deploy.sh, against a fake kubectl ---------------------------------------------------------------------
FAKE_KUBECTL = r"""#!/usr/bin/env bash
# Answers deploy.sh's reads from files in $FAKE, records every call, and fails the first apply of a manifest after
# the volumes, so the test stops there
echo "$*" >> "$FAKE/calls"
args=" $* "
case "$args" in
  *" version "*) exit 0 ;;
  *" config view "*) echo https://fake-cluster:6443 ;;
  *" get crd "*|*" get apiservice "*) exit 1 ;;
  *" get storageclass -o json "*) cat "$FAKE/classes.json" ;;
  *" get pvc -o json "*) cat "$FAKE/pvcs.json" ;;
  *" get statefulset -o json "*) cat "$FAKE/statefulsets.json" ;;
  *" get pvc "*" -o json "*) cat "$FAKE/grown.json" ;;
  *"/10-jobdata.yaml "*) echo "stop here" >&2; exit 1 ;;
esac
exit 0
"""


def _deploy(tmp_path: Path, answer: str, *options: str, **settings: str) -> tuple[subprocess.CompletedProcess, list]:
    work = tmp_path / "repo"
    shutil.copytree(ROOT / "scripts", work / "scripts")
    shutil.copytree(ROOT / "deploy", work / "deploy", ignore=shutil.ignore_patterns("*.env", ".rendered", ".last-build"))
    shutil.copytree(ROOT / "config", work / "config")
    k8s = work / "deploy" / "k8s"
    settings = {"STORAGE_CLASS": "block", "SHARED_STORAGE_CLASS": "nfs", "ENGINE_TAG": "t", "PORTAL_TAG": "t",
                "WEB_TAG": "t", "NODE_ENCRYPTION": "off", "KUBECONFIG_PATH": str(tmp_path / "kubeconfig"), **settings}
    lines = [ln for ln in (k8s / "deploy.env.example").read_text().splitlines() if ln.split("=", 1)[0] not in settings]
    (k8s / "deploy.env").write_text("\n".join(lines + [f"{k}={v}" for k, v in settings.items()]) + "\n")
    (k8s / "engine.env").write_text("REDIS_PASSWORD=aa\nLITELLM_DB_PASSWORD=bb\n")
    (k8s / "portal.env").write_text("PORTAL_DB_PASSWORD=cc\n")
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    kubectl = fake / "bin" / "kubectl"
    kubectl.write_text(FAKE_KUBECTL)
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    (fake / "pvcs.json").write_text(json.dumps({"items": LIVE + [pvc("data-redis-0", "1Gi"), pvc("grafana-data", "1Gi")]}))
    (fake / "statefulsets.json").write_text(json.dumps({"items": SETS + [sts("redis", "1Gi")]}))
    (fake / "classes.json").write_text(json.dumps({"items": CLASSES}))
    (fake / "grown.json").write_text(json.dumps(pvc("data-seaweedfs-0", "100Gi")))
    env = {**os.environ, "PATH": f"{fake / 'bin'}:{os.environ['PATH']}", "FAKE": str(fake), "GHCR_PULL_TOKEN": "x"}
    run = subprocess.run(["bash", "scripts/deploy.sh", *options], cwd=work, input=answer, capture_output=True,
                         text=True, timeout=120, env=env)
    calls = (fake / "calls").read_text().splitlines() if (fake / "calls").exists() else []
    return run, calls


needs_tools = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("perl")), reason="needs bash and perl")


@needs_tools
def test_the_plan_shows_what_will_grow_and_no_changes_nothing(tmp_path):
    run, calls = _deploy(tmp_path, "n\n", FILES_SIZE="100Gi")
    assert "deploy: stopped" in run.stderr
    assert "volumes to grow" in run.stdout and "data-seaweedfs-0: 50Gi to 100Gi (FILES_SIZE)" in run.stdout
    assert "statefulset/seaweedfs made again for 100Gi" in run.stdout
    assert not any(c.startswith(("patch", "delete", "apply")) or " patch " in c or " delete " in c for c in calls)


@needs_tools
def test_yes_grows_the_claim_and_recreates_the_statefulset_before_applying(tmp_path):
    run, calls = _deploy(tmp_path, "", "-y", FILES_SIZE="100Gi")
    assert "stop here" in run.stderr  # the fake's first apply after the volumes
    patch = next(i for i, c in enumerate(calls) if " patch pvc data-seaweedfs-0 " in f" {c} ")
    assert '"storage":"100Gi"' in calls[patch]
    delete = next(i for i, c in enumerate(calls) if "delete statefulset seaweedfs --cascade=orphan" in c)
    jobdata = next(i for i, c in enumerate(calls) if "10-jobdata.yaml" in c)
    assert patch < delete < jobdata
    assert "volume data-seaweedfs-0 is 100Gi, its filesystem too: nothing to restart" in run.stderr
    assert not any(" patch pvc data-postgres-0 " in f" {c} " for c in calls)  # its size didn't change


@needs_tools
def test_a_smaller_size_stops_before_asking(tmp_path):
    run, calls = _deploy(tmp_path, "y\n", DB_SIZE="5Gi")
    assert run.returncode != 0
    assert "can't shrink" in run.stderr and "stopped before changing anything" in run.stderr
    assert "Go ahead?" not in run.stdout and not any(" patch " in f" {c} " for c in calls)
