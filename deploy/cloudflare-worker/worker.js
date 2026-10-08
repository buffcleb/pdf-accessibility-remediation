// The web app served from Cloudflare's edge (optional; the default serves it from the cluster). Static files come
// from the assets binding (web/dist, with web/public/_headers); /api/ is forwarded to the portal API on the cluster
// through the tunnel, so the app and its API stay on one origin (the sign-in cookie needs that).
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) {
      return env.ASSETS.fetch(request);
    }
    const target = new URL(url.pathname + url.search, env.API_ORIGIN);
    const headers = new Headers(request.headers);
    // The caller's address, for the audit log (the portal reads x-client-ip in this mode). Always overwritten,
    // so a caller can't choose it.
    headers.set("x-client-ip", request.headers.get("cf-connecting-ip") ?? "");
    headers.set("x-forwarded-proto", "https");
    headers.set("x-forwarded-host", url.host);
    // The origin is behind Cloudflare Access; the Worker signs in with a service token
    if (env.ACCESS_CLIENT_ID && env.ACCESS_CLIENT_SECRET) {
      headers.set("cf-access-client-id", env.ACCESS_CLIENT_ID);
      headers.set("cf-access-client-secret", env.ACCESS_CLIENT_SECRET);
    }
    return fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual",
    });
  },
};
