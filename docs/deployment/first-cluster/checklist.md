# Checklists

[Deployment](../README.md) › [First cluster](README.md) › Checklists

One checklist per team, then the joint go-live checklist. Each item says how to check it. Copy this page into the
ticket or document where the teams sign off, and fill in the last column.

## Decided before install

These can't easily be changed later ([why](README.md)). Agree them first, together.

| Decision | Choice | Agreed by |
|---|---|---|
| Pod and service address ranges (clear of every campus network and VPN) | | Network, middleware |
| Three server nodes, their names and addresses | | OS, network |
| One floating address for API and users, or two; kube-vip or the campus load balancer | | Network, middleware |
| `<service-name>` and `<api-name>` | | Network, service owner |
| How the certificate is issued (cert-manager with an ACME service, or supplied) | | Network, middleware |
| The ingress controller ([note](middleware.md#cluster-add-ons)) | | Middleware |
| The storage classes (block and shared, [options](storage.md#where-block-storage-can-come-from)), and how volumes are backed up | | Storage, middleware |
| Online or air-gapped install; a campus registry mirror or not | | Middleware, network |
| The CIS hardening profile, or not | | OS, middleware, security office |
| Where secrets are kept (1Password vault, who has access) | | Middleware, service owner |

## Network

| Item | How to check | Done |
|---|---|---|
| Three node addresses, on one subnet | `ping` between the nodes | |
| Floating address(es) reserved and unused | `ping <api-vip>` gets no answer before install | |
| DNS: `<service-name>`, `<api-name>`, each node forward and reverse | `dig +short <name>` and `dig +short -x <address>` | |
| Firewall between the nodes ([table](network.md#firewall)), including UDP 51820 | From one node: `nc -zv <other-node> 6443` (after install) | |
| Users reach 443 (and 80) on the service address only | From a client network: `curl -I http://<service-name>` redirects after go-live | |
| Administrators reach 6443 and SSH from the admin network only | From elsewhere, both time out | |
| Outbound from the nodes ([list](network.md#from-the-nodes)), or the registry mirror | From a node: `curl -I https://ghcr.io` | |
| Outbound for the AI provider, Adobe (if used) and the mail relay | From a node: `curl -I https://bedrock-runtime.<region>.amazonaws.com` | |
| Mail relay allows the node addresses | A test mail from a node | |
| The certificate's issuer is reachable, or the certificate is ready | | |
| Backup share exported to the node addresses (if `BACKUPS=cronjob`) | `showmount -e <nas>` from a node | |

## Servers

| Item | How to check | Done |
|---|---|---|
| Three servers: 8 vCPU, 16 GB (scheduled maintenance) or 32 GB, ~150 GB SSD each ([why](os.md#how-much-memory)) | `nproc; free -g; df -h /var/lib` | |
| A second disk on each server, if storage is Longhorn ([storage](storage.md)) | `lsblk` | |
| A supported OS, patched | `cat /etc/os-release` | |
| Unique hostnames and machine IDs | `hostnamectl --static; cat /etc/machine-id` on each | |
| Time synchronized | `timedatectl` says `synchronized: yes` | |
| Swap off, now and after reboot | `swapon --show` prints nothing | |
| WireGuard in the kernel | `modprobe wireguard` succeeds | |
| NetworkManager leaves the cluster's interfaces alone | `/etc/NetworkManager/conf.d/rke2-canal.conf` exists | |
| Host firewall off, or set up as on the [servers page](os.md#host-firewall) | | |
| SELinux enforcing (RHEL family) | `getenforce` | |
| Security software excludes the cluster's paths | | |
| Storage clients installed (NFS, iSCSI as the storage team asks) | | |
| Patching procedure agreed: one node at a time, drained first | | |
| The middleware group's `sudo` rights, if it has no root ([root access](root-access.md)) | `sudo -l -U <a-member>` lists them | |
| Drain on shutdown installed, and the hypervisor's shutdown timeout at least 15 minutes ([how](os.md#drain-on-shutdown)) | `systemctl is-enabled rke2-drain` says `enabled` | |

## Storage

| Item | How to check | Done |
|---|---|---|
| A block storage class, with a Retain reclaim policy ([storage](storage.md)) | `kubectl get storageclass`: its `RECLAIMPOLICY` says `Retain` | |
| A shared (ReadWriteMany) storage class that user 10001 can write | A test claim from each becomes `Bound` | |
| Volumes reachable from every node (a pod moved to another node still finds its volume) | Drain the node a test pod is on; it starts elsewhere with its data | |
| How volumes are backed up and how long kept | | |

## Cluster and service

| Item | How to check | Done |
|---|---|---|
| RKE2 `<rke2-version>` on three servers, all `Ready` | `kubectl get nodes` | |
| Pod and service ranges as decided | `/etc/rancher/rke2/config.yaml` | |
| Graceful node shutdown set | `kubelet-arg` in `/etc/rancher/rke2/config.yaml` | |
| WireGuard on, on every node | `scripts/check_cluster_encryption.sh` ends `encrypted: WireGuard on 3 of 3 nodes` | |
| Secrets encrypted in etcd | `rke2 secrets-encrypt status` says `Enabled` | |
| etcd snapshots scheduled, and copied off the cluster | `rke2 etcd-snapshot list` | |
| Floating address answers and moves | Stop `rke2-server` on the node holding it; it moves | |
| cert-manager (if used) and its issuer ready | `kubectl get clusterissuer` says `True` | |
| KEDA in namespace `keda` | `kubectl get pods -n keda` | |
| metrics-server | `kubectl top nodes` shows figures | |
| Administrator kubeconfig in 1Password; read-only access for others | | |
| `deploy.env`, `portal.env` set; secrets in 1Password | | |
| Service deployed | `scripts/deploy.sh` ends with `done` | |
| HTTPS with a trusted certificate | The browser shows no warning; `curl -I https://<service-name>` | |

## Go-live

| Item | How to check | Done |
|---|---|---|
| A bootstrap super admin signs in (the code arrives by email) | | |
| A test document is remediated and downloaded | Upload a short PDF; it finishes | |
| A long document makes the workers scale up | Upload a large PDF; `kubectl get pods -n pdf-remediation` shows more `engine-worker` pods | |
| The audit log shows the user's real address | Administration › Reports › Audit log | |
| Email reaches the super admins (alerts go the same way) | Administration › Settings: "Send a test email" | |
| Backups run, and one has been restored into a scratch copy | [Backups](../../operations/backups-and-migrations.md#trying-a-restore) | |
| A node can go down: drain one, the service keeps working, uncordon it | [Patching](os.md#patching) | |
| The CISO review's open items accepted or done | [From POC to production](production.md) | |
| Who to call, written down: service owner, each team, the security office | | |
