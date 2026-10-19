#!/bin/sh
# Grafana at /grafana (#397), for super admins only, when GRAFANA_UPSTREAM names it (http://grafana:3000; set by the
# cluster's MONITORING=on). Every request there is checked with the portal first (auth_request): the browser carries
# a short-lived cookie the portal gave it when a super admin chose "Open Grafana", and the portal answers with their
# email, which goes to Grafana as the user (its auth proxy). Without the cookie, or for anyone else, it's 401, and the
# person is sent to the admin area. Any X-WEBAUTH-USER a caller sends is replaced, never passed on.
#
# Run by the nginx image's entrypoint before the templates are rendered.
set -eu

dir=/etc/nginx/conf.d/locations
mkdir -p "$dir"
out="$dir/grafana.conf"
if [ -z "${GRAFANA_UPSTREAM:-}" ]; then
  rm -f "$out" /etc/nginx/conf.d/00-grafana.conf
  exit 0
fi
case "$GRAFANA_UPSTREAM" in
  http://*|https://*) ;;
  *) echo "GRAFANA_UPSTREAM: '$GRAFANA_UPSTREAM' isn't an http(s) address" >&2; exit 1 ;;
esac
case "$GRAFANA_UPSTREAM$PORTAL_API_UPSTREAM" in
  *[!A-Za-z0-9.:/_-]*) echo "GRAFANA_UPSTREAM and PORTAL_API_UPSTREAM are plain addresses (letters, digits, . : / _ -)" >&2; exit 1 ;;
esac

# Grafana's live updates use WebSockets
cat > /etc/nginx/conf.d/00-grafana.conf <<'MAP'
map $http_upgrade $grafana_connection {
    default upgrade;
    "" close;
}
MAP

cat > "$out.tmp" <<CONF
# Grafana for super admins (docker/web/16-grafana.sh, #397)
location = /grafana-auth {
    internal;
    proxy_pass ${PORTAL_API_UPSTREAM}/api/v1/monitoring/grafana-auth;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Cookie \$http_cookie;
    proxy_set_header Host \$host;
}

location /grafana/ {
    auth_request /grafana-auth;
    auth_request_set \$grafana_user \$upstream_http_x_grafana_user;
    error_page 401 = @grafana_signin;
    proxy_pass ${GRAFANA_UPSTREAM};
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-WEBAUTH-USER \$grafana_user;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$grafana_connection;
    proxy_read_timeout 300s;
    # Grafana's pages carry their own scripts and styles: its own headers, not the app's Content-Security-Policy
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
}

location @grafana_signin {
    absolute_redirect off;  # the address as the browser knows it, not this container's port
    return 302 /admin/system?grafana=signin;
}
CONF
mv "$out.tmp" "$out"
echo "$0: Grafana at /grafana -> $GRAFANA_UPSTREAM (super admins, checked with the portal)"
