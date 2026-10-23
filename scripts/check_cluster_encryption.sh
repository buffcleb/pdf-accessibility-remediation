#!/usr/bin/env bash
# Is traffic between the cluster's nodes encrypted (#452)? Read-only: it asks the cluster and changes nothing.
#
#   scripts/check_cluster_encryption.sh                  the cluster in $KUBECONFIG (or ~/.kube/config)
#   scripts/check_cluster_encryption.sh --context NAME   a context in it
#
# What it understands:
# - Canal (RKE2's default) or flannel: the backend in Canal's settings, and the backend each node reports. Encrypted
#   when both say WireGuard on every node. The setting without the nodes means the network plugin hasn't been
#   restarted since it was changed.
# - Cilium (WireGuard or IPsec turned on in cilium-config) and Calico (WireGuard in its Felix settings).
# - A cluster of one node: pod traffic never leaves it.
#
# It prints a line per node where it can, then a verdict. Exit status: 0 encrypted, 1 not encrypted, 2 can't tell
# (another network plugin, or the cluster can't be read). The first-cluster guide sets it up:
# docs/deployment/first-cluster/middleware.md.
set -Euo pipefail

KC=(kubectl --request-timeout=20s)
while [ $# -gt 0 ]; do
  case "$1" in
    --context) KC+=(--context "${2:?--context needs a name}"); shift ;;
    -h|--help) sed -n '2,/^set -Euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

verdict() { echo "$2"; exit "$1"; }

"${KC[@]}" get nodes >/dev/null 2>&1 || verdict 2 "can't tell: the cluster can't be read (check KUBECONFIG and access)"
nodes="$("${KC[@]}" get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"
count="$(printf '%s\n' "$nodes" | grep -c .)"
[ "$count" -le 1 ] && verdict 0 "encrypted: not needed, the cluster has one node and pod traffic never leaves it"

# Canal or flannel: the setting, then what each node reports
conf=""
for cm in rke2-canal-config canal-config kube-flannel-cfg; do
  conf="$("${KC[@]}" -n kube-system get configmap "$cm" -o jsonpath='{.data.net-conf\.json}' 2>/dev/null)" && [ -n "$conf" ] && break
done
backends="$("${KC[@]}" get nodes -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.annotations.flannel\.alpha\.coreos\.com/backend-type}{"\n"}{end}')"
if [ -n "$conf" ] || printf '%s\n' "$backends" | grep -q '=.'; then
  setting="$(printf '%s' "$conf" | tr -d ' \n' | grep -o '"Type":"[^"]*"' | head -1 | cut -d'"' -f4)"
  echo "network plugin: Canal/flannel, backend setting: ${setting:-unknown}"
  on=0
  while IFS='=' read -r node backend; do
    [ -n "$node" ] || continue
    echo "  $node: ${backend:-no backend reported}"
    case "$backend" in wireguard*) on=$((on + 1)) ;; esac
  done <<< "$backends"
  # No setting found (flannel set up some other way): the nodes' own reports decide
  case "$setting" in wireguard*|"") wanted=1 ;; *) wanted=0 ;; esac
  if [ "$on" = "$count" ] && [ "$wanted" = 1 ]; then
    verdict 0 "encrypted: WireGuard on $on of $count nodes"
  elif [ "$wanted" = 1 ]; then
    verdict 1 "not encrypted yet: WireGuard is set, but only $on of $count nodes use it; restart the network plugin (kubectl -n kube-system rollout restart ds rke2-canal)"
  else
    verdict 1 "not encrypted: traffic between nodes is plain (backend ${setting:-unknown}); see docs/deployment/first-cluster/middleware.md"
  fi
fi

# Cilium
if "${KC[@]}" -n kube-system get configmap cilium-config >/dev/null 2>&1; then
  echo "network plugin: Cilium"
  cilium() { "${KC[@]}" -n kube-system get configmap cilium-config -o jsonpath="{.data.$1}"; }
  [ "$(cilium enable-wireguard)" = true ] && verdict 0 "encrypted: Cilium's WireGuard encryption is on"
  [ "$(cilium enable-ipsec)" = true ] && verdict 0 "encrypted: Cilium's IPsec encryption is on"
  verdict 1 "not encrypted: Cilium's encryption is off (enable-wireguard in cilium-config)"
fi

# Calico
calico="$("${KC[@]}" get felixconfiguration default -o jsonpath='{.spec.wireguardEnabled}' 2>/dev/null)" && {
  echo "network plugin: Calico"
  [ "$calico" = true ] && verdict 0 "encrypted: Calico's WireGuard is on"
  verdict 1 "not encrypted: Calico's WireGuard is off (wireguardEnabled in its Felix settings)"
}

verdict 2 "can't tell: a network plugin this check doesn't know; check its documentation for encryption between nodes"
