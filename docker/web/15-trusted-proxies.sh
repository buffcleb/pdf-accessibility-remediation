#!/bin/sh
# The caller's address behind a reverse proxy or load balancer (Caddy, nginx, an ALB, an ingress controller).
#
# By default this nginx reports its own peer as the caller (X-Forwarded-For: $remote_addr), so nobody can make up
# an address by sending the header themselves. Behind a proxy that peer is the proxy. Name the proxies you trust in
# TRUSTED_PROXIES (addresses or CIDR ranges, separated by commas or spaces) and nginx takes the caller's address
# from the X-Forwarded-For they send: the last address in it that isn't one of them.
#
# Run by the nginx image's entrypoint before the templates are rendered.
set -eu

out=/etc/nginx/conf.d/00-trusted-proxies.conf
proxies=$(printf '%s' "${TRUSTED_PROXIES:-}" | tr ',' ' ')
if [ -z "$(printf '%s' "$proxies" | tr -d ' ')" ]; then
  rm -f "$out"
  exit 0
fi

{
  echo "# From TRUSTED_PROXIES (docker/web/15-trusted-proxies.sh)"
  for p in $proxies; do
    # Addresses and CIDR ranges only (IPv4 or IPv6): this goes into nginx's configuration
    case "$p" in
      *[!0-9a-fA-F.:/]*|"")
        echo "TRUSTED_PROXIES: '$p' isn't an address or a CIDR range (like 192.0.2.0/24 or 2001:db8::/32)" >&2
        exit 1 ;;
    esac
    echo "set_real_ip_from $p;"
  done
  echo "real_ip_header X-Forwarded-For;"
  echo "real_ip_recursive on;"
} > "$out.tmp"
mv "$out.tmp" "$out"
echo "$0: trusting X-Forwarded-For from: $proxies"
