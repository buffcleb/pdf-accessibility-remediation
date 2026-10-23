"""The engine's own key for the AI gateway (#457), managed from inside the gateway's pod: scripts/deploy.sh runs it there
with `kubectl exec`, so the master key never leaves the pod. Standard library only (it runs in the LiteLLM image).

    python3 gateway_key.py check      stdin {"key": …}: exit 0 if the gateway knows the key, 1 if not
    python3 gateway_key.py generate   stdin {"models": […], "max_budget": …?}: prints a new key
    python3 gateway_key.py update     stdin {"key": …, "models": […], "max_budget": …?}: sets the key's models
    python3 gateway_key.py delete     stdin {"key": …}: revokes the key

The key can call only the models listed (config/litellm.yaml's names), and nothing that manages the gateway (keys,
users, settings, spend logs): those need the master key. The key's details travel on stdin, never in a command line.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("GATEWAY_URL", "http://localhost:4000")
ALIAS = "pdfrem-engine"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(f"{BASE}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {os.environ['LITELLM_MASTER_KEY']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - the gateway in its own pod
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def limits(args: dict) -> dict:
    """What the key may do: these models only, and a spending cap per 30 days when one is given."""
    out = {"models": list(args["models"])}
    if args.get("max_budget"):
        out |= {"max_budget": float(args["max_budget"]), "budget_duration": "30d"}
    return out


def main(command: str, args: dict) -> int:
    if command == "check":
        status, _ = call("GET", "/key/info?" + urllib.parse.urlencode({"key": args["key"]}))
        return 0 if status == 200 else 1
    if command == "generate":
        status, out = call("POST", "/key/generate", {**limits(args), "key_alias": ALIAS,
                                                      "metadata": {"for": "the PDF engine (#457)"}})
        if status != 200 or not out.get("key"):
            print(f"the gateway didn't make a key (HTTP {status})", file=sys.stderr)
            return 1
        print(out["key"])
        return 0
    if command == "update":
        status, _ = call("POST", "/key/update", {"key": args["key"], **limits(args)})
        return 0 if status == 200 else 1
    if command == "delete":
        status, _ = call("POST", "/key/delete", {"keys": [args["key"]]})
        return 0 if status == 200 else 1
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "", json.loads(sys.stdin.read() or "{}")))
