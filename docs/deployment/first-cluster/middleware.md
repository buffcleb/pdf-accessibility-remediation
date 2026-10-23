# Cluster and service

[Deployment](../README.md) › [First cluster](README.md) › Cluster and service

For the middleware team: installing the cluster on the three servers the OS team prepared, its add-ons, deploying the
service, and running it day to day. New to Kubernetes? The [start page](README.md#kubernetes-in-one-page) explains
the terms in a page.

**Values to fill in:**

| Value | From | Example |
|---|---|---|
| `<rke2-version>` | You: a current patch release of a supported minor version | `v1.33.13+rke2r1` |
| `<cluster-token>` | You: a long random string, kept in 1Password | `openssl rand -hex 32` |
| `<api-vip>`, `<api-name>`, `<service-vip>`, `<service-name>` | [Network team](network.md#addresses) | |
| `<pod-range>`, `<service-range>` | [Network team](network.md#addresses) | `10.42.0.0/16`, `10.43.0.0/16` |
| `<node-subnet>` | Network team | `192.0.2.0/24` |
| `<block-class>`, `<shared-class>` | Storage team: the storage classes | |
| `<issuer>` | You and networking: how certificates are issued | [HTTPS for the service](#https-for-the-service) |

Every command here is run as root on a server node (prefix `sudo`), or from an administrator's machine with
`kubectl`, as it says. Without root on the servers, [root access](root-access.md) lists each `sudo` command, what it
does and why it needs root, with a `sudoers` rule for the OS team.

## Online or air-gapped

RKE2 can be installed **online** (it downloads its files and images from the internet; the
[outbound access](network.md#from-the-nodes) the network team opens) or **air-gapped** (from files copied onto the
servers, and images from a campus registry). Online is simpler. Air-gapped means nothing on the nodes depends on the
internet. This page shows online; RKE2's [air-gap guide](https://docs.rke2.io/install/airgap) has the other.

## Install the cluster

The cluster is three server nodes. The first one starts the cluster; the other two join it.

### 1. The settings, on the first server

Before starting anything, write `/etc/rancher/rke2/config.yaml`:

```yaml
token: <cluster-token>
tls-san:                    # the names and address the API's certificate covers
  - <api-name>
  - <api-vip>
cluster-cidr: <pod-range>   # Decide at install: can't be changed later
service-cidr: <service-range>
cni: canal
write-kubeconfig-mode: "0600"
etcd-snapshot-schedule-cron: "0 */6 * * *"   # the cluster's own database, every 6 hours
etcd-snapshot-retention: 28                  # a week of them
kubelet-arg:                # a plain shutdown stops this server's pods properly first (servers page: shutting down)
  - "shutdown-grace-period=120s"
  - "shutdown-grace-period-critical-pods=30s"
# selinux: true             # RHEL-family servers (SELinux enforcing)
# profile: cis              # only if the OS team prepared for the CIS profile (servers page)
```

And turn on **WireGuard** for traffic between nodes, in
`/var/lib/rancher/rke2/server/manifests/rke2-canal-config.yaml`:

```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: rke2-canal
  namespace: kube-system
spec:
  valuesContent: |-
    flannel:
      backend: "wireguard"
```

Set both before the first start: they're far easier now than on a running cluster.

### 2. Install and start RKE2 on the first server

```bash
curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION=<rke2-version> sh -
```

```bash
systemctl enable --now rke2-server.service
```

The first start takes a few minutes. Follow it with `journalctl -u rke2-server -f`; it's ready when the log settles
and this shows the node `Ready`:

```bash
/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get nodes
```

> [!TIP]
> To use `kubectl` on a server: `export KUBECONFIG=/etc/rancher/rke2/rke2.yaml PATH=$PATH:/var/lib/rancher/rke2/bin`.
> That file gives full control of the cluster: keep it on the servers, and copy it only as described in
> [access](#access).

### 3. The floating address

Before the other servers join, give the cluster its floating address, so they (and you) can use `<api-vip>` rather
than the first server's own address.

- **With kube-vip:** install it as a DaemonSet on the server nodes, in ARP mode, for `<api-vip>`, following
  [kube-vip's RKE2 guide](https://kube-vip.io/docs/usage/rke2/). On a three-node cluster where every node is also a
  server, the simplest is one floating address for both the API and users' web traffic (`<service-vip>` the same as
  `<api-vip>`, with both names in DNS): the ingress controller runs on every node, so wherever the address is, 443 is
  answered there. Check: `ping <api-vip>` answers, and `curl -k https://<api-vip>:6443/version` replies (with a
  `401`, which is expected).
- **With the campus load balancer:** ask for 6443 and 9345 to the three nodes for `<api-vip>`, and 80 and 443 for
  `<service-vip>` ([networking](network.md#addresses)).

### 4. The other two servers

On each, the same `config.yaml` as the first, plus one line saying where to join:

```yaml
server: https://<api-vip>:9345
```

Copy `rke2-canal-config.yaml` too. Then install and start RKE2 exactly as in step 2, one server at a time, waiting for
each to show `Ready` in `kubectl get nodes` before starting the next.

### 5. Check the cluster

```bash
kubectl get nodes -o wide
```

Three nodes, all `Ready`, with the roles `control-plane,etcd,master`.

```bash
kubectl get pods -A
```

Everything `Running` or `Completed`, nothing in `CrashLoopBackOff` or `Pending` after a few minutes.

```bash
kubectl get cm rke2-canal-config -n kube-system -o jsonpath='{.data.net-conf\.json}'
```

Shows `"Type": "wireguard"`. On a node, `wg show` lists a peer for each of the other two. From an administrator's
machine, the service's own check does both:

```bash
scripts/check_cluster_encryption.sh
```

It ends `encrypted: WireGuard on 3 of 3 nodes`. `deploy.sh` runs it before every deploy and shows the result.

```bash
rke2 secrets-encrypt status
```

`Encryption Status: Enabled`: passwords and keys the cluster stores are encrypted on disk (RKE2's default).

```bash
rke2 etcd-snapshot list
```

After the first scheduled time, the cluster's database snapshots are listed.

## Cluster add-ons

Each one is a separate piece of software running in the cluster. Install them from an administrator's machine with
`kubectl` and [Helm](https://helm.sh/docs/intro/install/) (a package manager for Kubernetes), using
[the kubeconfig](#access). Pin each to a version and record it.

| Add-on | What it does | Comes with RKE2? |
|---|---|---|
| Canal | Pod networking, network policies, WireGuard | Yes |
| Traefik | Receives HTTPS for `<service-name>` and passes it to the service | Yes: the default from RKE2 v1.36 |
| metrics-server | CPU and memory figures (`kubectl top`), used for scaling | Yes |
| kube-vip | The floating address | No: installed above |
| cert-manager | HTTPS certificates, requested and renewed | No |
| KEDA | More engine workers when documents are waiting | No |
| The storage driver | Makes volumes from the storage team's storage | No: the storage team's |

> [!NOTE]
> **Traefik or ingress-nginx.** The upstream ingress-nginx project was retired in March 2026, and RKE2 v1.36 made
> Traefik the default for new clusters. RKE2 says not to expect new ingress-nginx images with fixes, and plans to
> remove it. Use Traefik on a new cluster: set `INGRESS_CONTROLLER=traefik` in the service's `deploy.env`.
> `INGRESS_CONTROLLER=nginx` stays for a cluster that still has ingress-nginx. An older RKE2 (v1.32.11, v1.33.7,
> v1.34.3 or later) gets Traefik with `ingress-controller: traefik` in `/etc/rancher/rke2/config.yaml` on each server
> node; see [moving a cluster](#moving-a-cluster-from-ingress-nginx-to-traefik) for one already serving.

### Traefik's settings for the cluster

Two things the service needs are settings of Traefik itself, not of the service's Ingress, so they're set once for
the cluster:
- **Time to upload:** Traefik stops reading a request after 60 seconds by default, which would cut off a large upload
  over a slow link. Allow about 15 minutes for the default `MAX_UPLOAD_SIZE` (100 MB); allow more if you raise it.
- **HTTP on to HTTPS:** RKE2's ingress-nginx did this by itself; Traefik needs to be told.

On RKE2, put this in `/var/lib/rancher/rke2/server/manifests/rke2-traefik-config.yaml` on a server node (RKE2
applies it and redeploys Traefik):

```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: rke2-traefik
  namespace: kube-system
spec:
  valuesContent: |-
    additionalArguments:
      # Uploads up to MAX_UPLOAD_SIZE over a slow link (Traefik's default: 60 s)
      - "--entryPoints.web.transport.respondingTimeouts.readTimeout=900s"
      - "--entryPoints.websecure.transport.respondingTimeouts.readTimeout=900s"
      # HTTP on to HTTPS
      - "--entryPoints.web.http.redirections.entryPoint.to=websecure"
      - "--entryPoints.web.http.redirections.entryPoint.scheme=https"
      # Only if a load balancer in front of the nodes sends the client's address in X-Forwarded-For: its addresses
      # - "--entryPoints.websecure.forwardedHeaders.trustedIPs=<lb-subnet>"
```

`web` and `websecure` are the entry point names in Traefik's chart. Check it took effect with
`kubectl -n kube-system get pods -l app.kubernetes.io/name=rke2-traefik`: the pods restart with the new arguments.
Downloads have no time limit (Traefik's write timeout is off by default), and Traefik doesn't buffer uploads.

### Moving a cluster from ingress-nginx to Traefik

RKE2's [migration guide](https://docs.rke2.io/reference/ingress_migration) runs both controllers side by side while you
check Traefik, then switches over. For this service:
1. Follow the guide's first phase (both controllers, Traefik on other ports), and apply
   [Traefik's settings](#traefiks-settings-for-the-cluster).
2. Test through Traefik as the guide says: sign in, upload a large PDF, download a result.
3. Switch over (the guide's third phase), then set `INGRESS_CONTROLLER=traefik` in `deploy.env` and leave
   `INGRESS_CLASS` empty (or set it to the class the guide gave Traefik), and deploy. The service's Ingresses are
   rewritten for Traefik; the guide's last phase removes the old ones it made.
4. Update `WEB_TRUSTED_PROXIES` if Traefik connects from different addresses (below).

### cert-manager

```bash
helm repo add jetstack https://charts.jetstack.io && helm repo update
```

```bash
helm install cert-manager jetstack/cert-manager --namespace cert-manager --create-namespace --version <version> --set crds.enabled=true
```

Check: `kubectl get pods -n cert-manager` shows three pods `Running`. Then the issuer: see
[HTTPS for the service](#https-for-the-service).

### KEDA

```bash
helm repo add kedacore https://kedacore.github.io/charts && helm repo update
```

```bash
helm install keda kedacore/keda --namespace keda --create-namespace --version <version>
```

Install it in the namespace **`keda`**: the service's network policy lets KEDA read the queue's length from the
database only from there. Check: `kubectl get pods -n keda` shows its pods `Running`.

### Storage

The storage team provides two storage classes, block (`<block-class>`) and shared (`<shared-class>`): what each is
for, why no disk is shared between servers, and the options when only NFS is on offer are on the
[storage page](storage.md). With Longhorn, the middleware team installs it here, on the second disk the OS team gave
each server. Check both classes before going further:

```bash
kubectl get storageclass
```

Both names listed. The block class's `RECLAIMPOLICY` column should say **`Retain`**, so deleting a volume's claim
doesn't destroy its data ([why](storage.md#two-kinds-of-storage-and-neither-shares-a-disk-between-servers)).

Then make a test claim from each class, see it become `Bound`, and delete it. The shared class must let a pod running as
user 10001 write (the engine's user): the service checks this when it starts and says so if it can't.

## HTTPS for the service

Choose one with the network team ([the certificate](network.md#the-certificate)):

- **cert-manager with an ACME service** (InCommon, Let's Encrypt). Make a ClusterIssuer: an example for HTTP-01:

  ```yaml
  apiVersion: cert-manager.io/v1
  kind: ClusterIssuer
  metadata:
    name: campus-acme
  spec:
    acme:
      server: <acme-directory-url>
      email: <team-mailbox>
      privateKeySecretRef: { name: campus-acme-account }
      # InCommon and some others also need externalAccountBinding (a key ID and secret they give you)
      solvers:
        - http01: { ingress: { ingressClassName: traefik } }  # nginx with ingress-nginx
  ```

  Apply it with `kubectl apply -f`, check `kubectl get clusterissuer` says `READY True`, and set
  `CERT_ISSUER=campus-acme` in the service's `deploy.env`. cert-manager then requests the certificate when the service
  is deployed and renews it before it expires.
- **A certificate you supply** (the campus CA). After the service's namespace exists (the first deploy makes it), store
  it, then set `INGRESS_TLS_SECRET=web-tls` in `deploy.env` and deploy again:

  ```bash
  kubectl create secret tls web-tls -n pdf-remediation --cert=<fullchain.pem> --key=<private-key.pem>
  ```

  Replace it the same way before it expires, adding `--dry-run=client -o yaml | kubectl apply -f -` to the same
  command so it updates the secret in place.

Either way, plain HTTP on port 80 is redirected to HTTPS.

## Access

- `/etc/rancher/rke2/rke2.yaml` on a server is the **administrator's kubeconfig**: full control of the cluster. Copy it
  to an administrator's machine, change its `server:` line to `https://<api-name>:6443`, and keep it in 1Password,
  not in a home directory or a share. Give it only to the people who run the cluster.
- **Read-only access** for others (the service owner, the help desk): a Kubernetes account bound to the built-in
  `view` role, limited to the `pdf-remediation` namespace. It can see pods and logs, not secrets.
- Only administrators may read secrets or `exec` into pods in `pdf-remediation`.
- Later: sign-in to the cluster with campus SSO (RKE2 supports OIDC) instead of shared files.

## Deploy the service

### What the administrator's machine needs

Linux or macOS with: `bash`, `git`, `kubectl`, `helm`, `perl` and `python3`; `docker` (to build the images) or access
to images built elsewhere; and the 1Password CLI (`op`) for the secrets. Plus the administrator's kubeconfig.

### Steps

> **Changed 22 October 2026:** the settings table names `EGRESS_PROXY` and `MALWARE_SCAN`, which production wants
> decided.

1. **Get the code:** clone the repository, at the release you're deploying.
2. **Make the settings files:**

   ```bash
   scripts/deploy.sh init
   ```

   It makes three files in `deploy/k8s/`, readable only by you: `deploy.env` (settings, no secrets), and `engine.env`
   and `portal.env` (secrets, with new random passwords and keys). Put the secrets in 1Password; the files can then be
   made from it each time instead of kept on disk.
3. **Edit `deploy/k8s/deploy.env`.** At least:

   | Setting | Value |
   |---|---|
   | `KUBECONFIG_PATH` | Your administrator's kubeconfig |
   | `APP_HOST` | `<service-name>` |
   | `REGISTRY` | Where the images are (GitHub's registry, or the campus mirror) |
   | `STORAGE_CLASS`, `SHARED_STORAGE_CLASS` | `<block-class>`, `<shared-class>` |
   | `CERT_ISSUER` or `INGRESS_TLS_SECRET` | [HTTPS for the service](#https-for-the-service) |
   | `INGRESS_CONTROLLER` | `traefik` ([Traefik or ingress-nginx](#cluster-add-ons)) |
   | `WEB_TRUSTED_PROXIES` | Where the ingress controller connects from, so the web app trusts the client address it passes on: `<node-subnet>` if it runs on the nodes' own network (RKE2's ingress-nginx does), else the pod network (RKE2's default `10.42.0.0/16`). `kubectl -n kube-system get pods -o wide` shows the controller's pod addresses: node addresses or pod ones |
   | `NODE_ENCRYPTION` | `require`: a deploy stops if traffic between the nodes isn't encrypted (`check` only shows it) |
   | `EGRESS_PROXY` | `log` at first, then `enforce` once the proxy's log shows nothing missing ([the egress proxy](../../../deploy/k8s/README.md#outbound-traffic-the-egress-proxy)) |
   | `MALWARE_SCAN` | `clamav` to scan uploads (about 2 GB of memory more), or `off` with the risk accepted ([malware scanning](../../operations/malware-scanning.md)) |
   | `BACKUPS` | `cronjob` with the backup share's server and path, or `external` if the storage team's snapshots cover the volumes ([backups](../../operations/backups-and-migrations.md)) |

   Every setting is explained in the file and in [`deploy/k8s/README.md`](../../../deploy/k8s/README.md).
4. **Edit `deploy/k8s/portal.env`:**
   - `PORTAL_CLIENT_IP_HEADER=x-forwarded-for`, so the audit log and rate limits see the user's address, not the
     ingress controller's;
   - the mail settings for `<smtp-relay>` ([email](../../operations/email-and-branding.md#email));
   - `PORTAL_BOOTSTRAP_SUPER_ADMINS`: the email addresses of the first super admins (they become super admins when
     they first sign in).
5. **Build and push the images** (or skip this and set the tags in `deploy.env` if they're built elsewhere):

   ```bash
   scripts/build.sh
   ```

6. **Deploy:**

   ```bash
   scripts/deploy.sh
   ```

   It shows where it's deploying and asks first. Then it makes the namespace and secrets, starts the database, queue
   and file store, **backs up the database** if one already exists, updates its structure, starts everything else and
   checks each part answers. It ends with `done` and the address. A failure says which step and why.
7. **Check it:** open `https://<service-name>`, sign in as a bootstrap super admin (the code arrives by email), upload a
   short PDF and watch it finish. Then work through the [go-live checklist](checklist.md#go-live).

Updating later is the same: `scripts/build.sh` (or new tags), then `scripts/deploy.sh`.

## Day to day

> **Changed 22 October 2026:** the runbooks are written (#454), and linked here.

Where to look when something seems wrong, in order:

1. **Administration › System** in the service (as a super admin): every part, whether it's running, its CPU and
   memory, and the commands for its logs.
2. **Grafana** at `https://<service-name>/grafana/` (super admins): history and alerts.
3. **The cluster:**

   ```bash
   kubectl get pods -n pdf-remediation
   ```

   Anything not `Running` (or `Completed`), or with a high `RESTARTS` count, is where to look.

   ```bash
   kubectl logs -n pdf-remediation deploy/portal-api --since=1h
   ```

   The last hour of a part's log (use its name from the list). `kubectl describe pod <pod-name> -n pdf-remediation`
   explains why a pod won't start.

```bash
scripts/deploy.sh status
```

A summary of what's running and which images.

Runbooks: [incident response](../../operations/incident-response.md),
[rotating secrets](../../operations/secret-rotation.md) and [rolling back](../../operations/rollback.md); backups and
restores are in [backups](../../operations/backups-and-migrations.md); monitoring in
[monitoring](../../operations/monitoring.md).

## Upgrading RKE2

Stay on a supported minor version (each is supported for about a year). Upgrade one minor version at a time, servers
one by one: drain the node, run the installer with the new `INSTALL_RKE2_VERSION`, restart `rke2-server`, check it's
`Ready`, uncordon, next. Read the release notes first, especially for the network plugin and ingress controller. Take
an etcd snapshot before starting (`rke2 etcd-snapshot save`). RKE2's
[upgrade guide](https://docs.rke2.io/upgrades/manual_upgrade) has the details, and an automated way.

## Checklist

The middleware team's checklist is on the [checklists page](checklist.md#cluster-and-service).
