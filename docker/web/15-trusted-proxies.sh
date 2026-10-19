#!/bin/sh
# The caller's address behind a reverse proxy or load balancer (Caddy, nginx, an ALB, an ingress controller).
#
# By default this nginx reports its own peer as the caller (X-Forwarded-For: $remote_addr), so nobody can make up
# an address by sending the header themselves. Behind a proxy that peer is the proxy. Name the proxies you trust in
# TRUSTED_PROXIES (addresses or CIDR ranges, separated by commas or spaces) and nginx takes the caller's address
# from the X-Forwarded-For they send: the last address in it that isn't one of them.
#
# Run by the nginx image's entrypoint before the templates are rendered.
#
# Cloudflare's own caller header (CF-Connecting-IP) is passed to the portal only from TUNNEL_PROXIES (cloudflared's
# address range); from anything else, such as an ingress controller on a node's address, it's sent empty, so nobody
# reaching the web app another way can name themselves to the portal (#336).
set -eu

valid() {  # addresses and CIDR ranges only (IPv4 or IPv6): they go into nginx's configuration
  case "$2" in
    *[!0-9a-fA-F.:/]*|"")
      echo "$1: '$2' isn't an address or a CIDR range (like 192.0.2.0/24 or 2001:db8::/32)" >&2
      exit 1 ;;
  esac
}

tunnel=/etc/nginx/conf.d/00-tunnel.conf
{
  echo "# From TUNNEL_PROXIES (docker/web/15-trusted-proxies.sh): the connection's own peer, before any real_ip"
  echo "geo \$realip_remote_addr \$from_tunnel {"
  echo "    default 0;"
  for p in $(printf '%s' "${TUNNEL_PROXIES:-}" | tr ',' ' '); do
    valid TUNNEL_PROXIES "$p"
    echo "    $p 1;"
  done
  echo "}"
  echo "map \$from_tunnel \$tunnel_caller {"
  echo "    1 \$http_cf_connecting_ip;"
  echo "    default \"\";"
  echo "}"
} > "$tunnel.tmp"
mv "$tunnel.tmp" "$tunnel"

out=/etc/nginx/conf.d/00-trusted-proxies.conf
proxies=$(printf '%s' "${TRUSTED_PROXIES:-}" | tr ',' ' ')
if [ -z "$(printf '%s' "$proxies" | tr -d ' ')" ]; then
  rm -f "$out"
  exit 0
fi

{
  echo "# From TRUSTED_PROXIES (docker/web/15-trusted-proxies.sh)"
  for p in $proxies; do
    valid TRUSTED_PROXIES "$p"
    echo "set_real_ip_from $p;"
  done
  echo "real_ip_header X-Forwarded-For;"
  echo "real_ip_recursive on;"
} > "$out.tmp"
mv "$out.tmp" "$out"
echo "$0: trusting X-Forwarded-For from: $proxies"
