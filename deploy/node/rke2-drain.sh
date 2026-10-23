#!/usr/bin/env bash
# Drain this node before it shuts down, and let pods back onto it after it boots, so a plain `shutdown` or `reboot`
# (or the hypervisor shutting the VM down) moves the service's pods off in an orderly way first. Run by
# rke2-drain.service (installed as on docs/deployment/first-cluster/os.md#drain-on-shutdown):
#
#   rke2-drain.sh down    at shutdown: drain, unless another node is already out
#   rke2-drain.sh up      at boot: once this node is Ready, uncordon it, if this script drained it
#
# It never stops a shutdown: when the drain can't run or doesn't finish, the shutdown goes ahead (the data is safe;
# the cluster moves the pods once it notices the node has gone).
set -u

KUBECTL="${KUBECTL:-/var/lib/rancher/rke2/bin/kubectl}"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/rke2/rke2.yaml}"
NODE="${NODE_NAME:-$(hostname)}"
MARK="${DRAIN_MARK:-/var/lib/rke2-drain.drained}"
TIMEOUT="${DRAIN_TIMEOUT:-10m}"

k() { "$KUBECTL" --request-timeout=20s "$@"; }

case "${1:-}" in
  down)
    # Two nodes drained at once would leave the service nowhere to go (and the cluster's database without a
    # majority): only drain when every other node is up and taking pods
    others="$(k get nodes --no-headers 2>/dev/null | awk -v n="$NODE" '$1 != n && $2 != "Ready"' | wc -l)"
    if ! k get nodes >/dev/null 2>&1; then
      echo "rke2-drain: the cluster can't be reached; shutting down without draining"
    elif [ "$others" -gt 0 ]; then
      echo "rke2-drain: another node is down or cordoned; shutting down without draining"
    elif k drain "$NODE" --ignore-daemonsets --delete-emptydir-data --timeout="$TIMEOUT"; then
      touch "$MARK"
      echo "rke2-drain: $NODE drained"
    else
      touch "$MARK"  # partly drained and cordoned: let it back in after boot all the same
      echo "rke2-drain: the drain didn't finish within $TIMEOUT; shutting down anyway"
    fi
    exit 0 ;;
  up)
    [ -f "$MARK" ] || exit 0  # not drained by this script (or cordoned on purpose by someone): leave it
    for _ in $(seq 90); do
      status="$(k get node "$NODE" --no-headers 2>/dev/null | awk '{print $2}')"
      case "$status" in Ready*) break ;; esac
      sleep 10
    done
    if k uncordon "$NODE"; then rm -f "$MARK"; echo "rke2-drain: $NODE uncordoned"; fi
    exit 0 ;;
  *)
    echo "usage: $0 down|up" >&2
    exit 2 ;;
esac
