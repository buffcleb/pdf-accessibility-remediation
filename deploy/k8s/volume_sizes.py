"""Growing a volume when its size in deploy.env goes up (#592): what scripts/deploy.sh has to do, worked out from the
cluster's claims, StatefulSets and storage classes (kubectl get ... -o json), before it applies anything.

    python3 volume_sizes.py plan PVCS STATEFULSETS CLASSES SPEC...   what to grow, one tab-separated line each
    python3 volume_sizes.py state PVC SIZE                           where one claim's growing has got to

Each SPEC is SETTING=SIZE:TARGET[:WORKLOAD]: TARGET is statefulset/NAME (its claims are data-NAME-0, data-NAME-1...,
from its volumeClaimTemplate "data") or pvc/NAME; WORKLOAD is what to restart for a claim's filesystem to grow (a
StatefulSet's is itself). The plan's lines:

    grow      CLAIM  FROM  TO  SETTING  WORKLOAD    patch the claim's request (it grows only on a class that allows it)
    recreate  NAME   FROM  TO  SETTING              delete the StatefulSet with --cascade=orphan (its pods and claims
                                                    keep running) so the apply that follows makes it with the new size

A size smaller than the claim's (volumes never shrink), a class without allowVolumeExpansion and a claim with no class
stop the deploy: the reasons go to stderr, one a line, and the exit status is 1. A claim that doesn't exist yet (a
first deploy) is left to the apply. state prints grown (its capacity is the new size), pending (the disk has grown;
the filesystem waits for the node: FileSystemResizePending) or resizing (the storage hasn't finished). Standard
library only.
"""
from __future__ import annotations

import json
import re
import sys
from decimal import Decimal, InvalidOperation

UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15, "E": 10**18,
         "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}


def size_bytes(quantity: str) -> int:
    """A Kubernetes quantity (50Gi, 10G, 1.5Ti, 1073741824) in bytes."""
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kMGTPE]i?|Ki)?", (quantity or "").strip())
    if not m:
        raise ValueError(quantity)
    try:
        return int(Decimal(m.group(1)) * UNITS[m.group(2) or ""])
    except (InvalidOperation, KeyError) as e:
        raise ValueError(quantity) from e


def _items(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    return (json.loads(text) if text else {}).get("items") or []


def _request(obj: dict) -> str:
    return ((obj.get("spec") or {}).get("resources") or {}).get("requests", {}).get("storage", "")


def _template_size(sts: dict) -> str:
    for t in (sts.get("spec") or {}).get("volumeClaimTemplates") or []:
        if (t.get("metadata") or {}).get("name") == "data":
            return _request(t)
    return ""


def plan(pvcs: list[dict], statefulsets: list[dict], classes: list[dict], specs: list[str]
         ) -> tuple[list[list[str]], list[str]]:
    """The steps (as plan prints them) and the reasons to stop."""
    claims = {p["metadata"]["name"]: p for p in pvcs}
    sets = {s["metadata"]["name"]: s for s in statefulsets}
    expandable = {c["metadata"]["name"]: bool(c.get("allowVolumeExpansion")) for c in classes}
    steps: list[list[str]] = []
    stop: list[str] = []
    for spec in specs:
        setting, _, rest = spec.partition("=")
        size, _, rest = rest.partition(":")
        target, _, workload = rest.partition(":")
        kind, _, name = target.partition("/")
        if not size:
            stop.append(f"{setting} isn't set in deploy/k8s/deploy.env")
            continue
        try:
            want = size_bytes(size)
        except ValueError:
            stop.append(f"{setting}={size} isn't a size Kubernetes understands (such as 50Gi)")
            continue
        if kind == "statefulset":
            mine = sorted(n for n in claims if re.fullmatch(rf"data-{re.escape(name)}-[0-9]+", n))
            workload = target
        else:
            mine = [name] if name in claims else []
        for claim in mine:
            pvc = claims[claim]
            have = _request(pvc)
            if want == size_bytes(have):
                continue
            if want < size_bytes(have):
                stop.append(f"{setting} is {size} but the volume {claim} is already {have}: a volume can't shrink. "
                            f"Set {setting}={have} in deploy/k8s/deploy.env (docs/deployment/first-cluster/"
                            f"storage.md#growing-a-volume)")
                continue
            cls = (pvc.get("spec") or {}).get("storageClassName") or ""
            if not cls:
                stop.append(f"{setting} is {size} but the volume {claim} ({have}) was made by hand, with no storage "
                            f"class, and can't be grown. Set {setting}={have} in deploy/k8s/deploy.env")
            elif not expandable.get(cls):
                stop.append(f"{setting} is {size} but the volume {claim} ({have}) is on the storage class {cls}, "
                            f"which doesn't allow volumes to grow (allowVolumeExpansion). Set {setting}={have} again, "
                            f"and ask whoever runs the storage to allow it, or move to a bigger volume with a backup "
                            f"and a restore (docs/deployment/first-cluster/storage.md#when-the-class-cant-grow-a-volume)")
            else:
                steps.append(["grow", claim, have, size, setting, workload])
        if kind == "statefulset" and name in sets:
            current = _template_size(sets[name])
            if current and size_bytes(current) != want:
                steps.append(["recreate", name, current, size, setting])
    return steps, stop


def state(pvc: dict, size: str) -> str:
    """grown, pending (the filesystem waits for the node) or resizing (the storage hasn't finished)."""
    status = pvc.get("status") or {}
    capacity = (status.get("capacity") or {}).get("storage", "")
    if capacity and size_bytes(capacity) >= size_bytes(size):
        return "grown"
    if any(c.get("type") == "FileSystemResizePending" and c.get("status") == "True"
           for c in status.get("conditions") or []):
        return "pending"
    return "resizing"


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "plan":
        steps, stop = plan(_items(argv[1]), _items(argv[2]), _items(argv[3]), argv[4:])
        for reason in stop:
            print(reason, file=sys.stderr)
        if stop:
            return 1
        for step in steps:
            print("\t".join(step))
        return 0
    if len(argv) == 3 and argv[0] == "state":
        with open(argv[1], encoding="utf-8") as f:
            print(state(json.load(f), argv[2]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
