# Root access for the middleware team

[Deployment](../README.md) › [First cluster](README.md) › Root access

> **New 22 October 2026.**

For the OS team, who grant it, and the middleware team, who use it. On many campuses the middleware team doesn't have
root on the servers: its members are in a group that's given `sudo` for what they need. This page lists every command
in this guide that the middleware team runs with `sudo` on a server node, what it does, and why it needs root, with a
`sudoers` rule to start from.

Everything else the middleware team does (the add-ons, deploying the service, day to day) runs from an administrator's
machine with `kubectl`, `helm` and the service's scripts, and needs no root anywhere. The service's own pods never run
as root ([what runs as root](os.md#what-runs-as-root)).

**Values to fill in:** `<middleware-group>`, the group the OS team gives the rights to.

## Two kinds of access

- **For the install, and for each RKE2 upgrade: full `sudo`, for a set time.** Installing RKE2 runs its installer as
  root. It adds a package repository (or unpacks files into `/usr/local`), installs packages and systemd services,
  and sets up SELinux's policy. However a `sudoers` rule is written, letting someone run an installer as root is
  letting them run anything as root. So give it for the install window (a day for the cluster's install, an hour or two
  per server for an upgrade), or have someone from the OS team run those steps with the middleware team.
- **Standing: the commands below, and nothing else.** Running and checking a cluster that's already installed: start
  and stop RKE2, read its log, edit its settings, read the cluster's own status.

> [!NOTE]
> **What a narrow rule does and doesn't do.** RKE2 runs as root, and its settings file and manifests folder say what
> it runs. Someone who can edit `/etc/rancher/rke2/config.yaml` or drop a file in
> `/var/lib/rancher/rke2/server/manifests/` can, with some effort, make RKE2 run something as root on that server. And
> the administrator's kubeconfig on each server gives full control of the cluster, as it's meant to. So the rule below
> keeps people to the commands the job needs, which prevents mistakes and leaves a clear `sudo` log of who did what.
> It isn't a wall between the middleware team and root on the servers; that's true of any Kubernetes cluster.

## The commands

### Installing and upgrading RKE2 (full sudo, for a set time)

| Command | What it does | Why it needs root | When |
|---|---|---|---|
| `curl -sfL https://get.rke2.io -o rke2-install.sh`, then `sudo INSTALL_RKE2_VERSION=<rke2-version> sh rke2-install.sh` | Downloads RKE2's installer and runs it. Download it and read it first, rather than piping it straight to `sh` | It installs packages (on RHEL-family systems it adds RKE2's repository and installs its RPMs, with `rke2-selinux`), and the `rke2-server` and `rke2-agent` systemd services | [Install](middleware.md#2-install-and-start-rke2-on-the-first-server), each [upgrade](middleware.md#upgrading-rke2), each [new agent node](scaling.md#3-more-nodes) (with `INSTALL_RKE2_TYPE=agent`) |
| `sudo mkdir -p /etc/rancher/rke2 /var/lib/rancher/rke2/server/manifests` | Makes the folders for RKE2's settings and manifests before the first start | Both are under root-owned folders; RKE2 reads them as root | Install, before the first start |
| `sudo install -m 0755 rke2-drain.sh /usr/local/sbin/`, `sudo install -m 0644 rke2-drain.service /etc/systemd/system/`, `sudo systemctl daemon-reload`, `sudo systemctl enable --now rke2-drain.service` | Installs the service's [drain on shutdown](os.md#drain-on-shutdown) (from `deploy/node/`), if the middleware team installs it rather than the OS team | Adds a program to `/usr/local/sbin` and a systemd service, which runs as root at every shutdown and boot | Install, once per server |

### Settings files (standing)

Edit them with `sudoedit` (it copies the file, opens your own editor as you, and writes it back as root), not by
running an editor with `sudo`, which would let you open a root shell from it.

| Command | What it does | Why it needs root | When |
|---|---|---|---|
| `sudoedit /etc/rancher/rke2/config.yaml` | RKE2's settings on that server: the cluster's token, address ranges, WireGuard, graceful shutdown; later, `ingress-controller: traefik` | The file holds the cluster's join token, so it's root's and `0600`. RKE2 reads it as root at start | [Install](middleware.md#1-the-settings-on-the-first-server), then rarely. A change takes effect when RKE2 restarts |
| `sudoedit /var/lib/rancher/rke2/server/manifests/rke2-canal-config.yaml` | Turns on WireGuard between the nodes | RKE2 applies everything in that folder to the cluster, as the cluster's administrator | Install, before the first start |
| `sudoedit /var/lib/rancher/rke2/server/manifests/rke2-traefik-config.yaml` | [Traefik's settings](middleware.md#traefiks-settings-for-the-cluster): the upload time and HTTP on to HTTPS | The same folder | Install, or when moving to Traefik |
| `sudoedit /var/lib/rancher/rke2/server/manifests/kube-vip.yaml` | The floating address, if [kube-vip](middleware.md#3-the-floating-address) is installed the way its RKE2 guide does it (it also uses RKE2's `ctr` to make the file: run that as part of the install window) | The same folder | Install |

### Running RKE2 (standing)

| Command | What it does | Why it needs root | When |
|---|---|---|---|
| `sudo systemctl enable --now rke2-server.service` | Starts RKE2 on a server node, and at every boot | Starting and stopping system services is root's | Install, each new server |
| `sudo systemctl enable --now rke2-agent.service` | The same on an agent node | The same | Each [new agent node](scaling.md#3-more-nodes) |
| `sudo systemctl restart rke2-server.service` (`rke2-agent` on an agent) | Restarts RKE2 on that server: after a settings change, and after an upgrade. Its pods keep running while it restarts | The same | Upgrades, settings changes |
| `sudo systemctl stop rke2-server.service`, then `start` | Stops RKE2 on one server | The same | The [checklist's](checklist.md#cluster-and-service) test that the floating address moves |
| `sudo journalctl -u rke2-server` (`-f` to follow, `-b` since boot; `-u rke2-agent`, `-u rke2-drain`) | RKE2's log on that server: the first thing to read when a node isn't `Ready` | System logs are readable by root, or by the `systemd-journal` group (`adm` on Ubuntu). Putting the group in that one instead needs no `sudo` | Install, troubleshooting |

### Checking the cluster from a server (standing)

| Command | What it does | Why it needs root | When |
|---|---|---|---|
| `sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get nodes` | `kubectl` on a server, with the administrator's kubeconfig RKE2 writes there | The kubeconfig is root's and `0600`: it gives full control of the cluster ([access](middleware.md#access)) | Install, before it's copied to an administrator's machine. After that, `kubectl` runs from the administrator's machine without `sudo` |
| `sudo cat /etc/rancher/rke2/rke2.yaml` | Shows the administrator's kubeconfig, to copy into 1Password | The same | Once, after install ([access](middleware.md#access)) |
| `sudo rke2 secrets-encrypt status` | Says whether the passwords and keys the cluster stores are encrypted on disk | It reads the cluster's administrator credentials on that server | Install, the [checklist](checklist.md#cluster-and-service) |
| `sudo rke2 etcd-snapshot list` and `sudo rke2 etcd-snapshot save` | Lists the cluster's database snapshots; takes one now, in the usual folder | Snapshots are written under `/var/lib/rancher/rke2/server/db/snapshots`, root's, from the cluster's own database. Its options aren't allowed: `--dir` would let it write anywhere as root | The checklist, before each upgrade |
| `sudo wg show` | The WireGuard tunnels to the other nodes | Reading network interfaces' keys and peers needs root. Also needs the `wireguard-tools` package (OS team). Optional: `scripts/check_cluster_encryption.sh` checks the same from an administrator's machine | Install, troubleshooting |
| `sudo /var/lib/rancher/rke2/bin/crictl --config /var/lib/rancher/rke2/agent/etc/crictl.yaml ps` | The containers on that server, seen by the container runtime directly (also `pods`, `inspect <id>`, `logs <id>`) | The runtime's socket is root's | Troubleshooting a node whose pods `kubectl` can't see |

The `rke2` command is `/usr/bin/rke2` when it's installed from RPMs and `/usr/local/bin/rke2` from the tarball
(Ubuntu, and SUSE without RPMs). Check with `command -v rke2` and use that path in the rule.

### Not for the standing rule

- **`rke2-uninstall.sh` and `rke2-killall.sh`.** They remove RKE2, or stop every container on the server. Not needed
  day to day; run them only in an install window, and only with the cluster's rebuild in mind.
- **Rebooting a server.** The OS team's ([patching](os.md#patching)). If the middleware team reboots too, add
  `/usr/bin/systemctl reboot`.
- **Anything under `/var/lib/rancher`, `/var/lib/kubelet` or `/run/k3s` other than the manifests above.** It's the
  cluster ([what not to do](os.md#what-not-to-do)).

## A `sudoers` rule to start from

In `/etc/sudoers.d/rke2-middleware` on every server, made with `visudo -f /etc/sudoers.d/rke2-middleware` (it checks
the file before saving: a mistake in `sudoers` can lock everyone out of `sudo`). Adjust the paths to the servers'
(`command -v systemctl journalctl rke2 wg`).

```sudoers
# The middleware team's standing rights on the cluster's servers (first-cluster guide, root access)
Cmnd_Alias RKE2_UNITS = \
    /usr/bin/systemctl enable --now rke2-server.service, /usr/bin/systemctl enable --now rke2-agent.service, \
    /usr/bin/systemctl start rke2-server.service,   /usr/bin/systemctl start rke2-agent.service, \
    /usr/bin/systemctl stop rke2-server.service,    /usr/bin/systemctl stop rke2-agent.service, \
    /usr/bin/systemctl restart rke2-server.service, /usr/bin/systemctl restart rke2-agent.service
# Only if the group isn't in systemd-journal (see below): these exact forms, no others
Cmnd_Alias RKE2_LOGS = \
    /usr/bin/journalctl -u rke2-server, /usr/bin/journalctl -u rke2-server -f, /usr/bin/journalctl -u rke2-server -b, \
    /usr/bin/journalctl -u rke2-agent,  /usr/bin/journalctl -u rke2-agent -f,  /usr/bin/journalctl -u rke2-agent -b, \
    /usr/bin/journalctl -u rke2-drain,  /usr/bin/journalctl -u rke2-drain -b
Cmnd_Alias RKE2_FILES = \
    sudoedit /etc/rancher/rke2/config.yaml, \
    sudoedit /var/lib/rancher/rke2/server/manifests/rke2-canal-config.yaml, \
    sudoedit /var/lib/rancher/rke2/server/manifests/rke2-traefik-config.yaml, \
    sudoedit /var/lib/rancher/rke2/server/manifests/kube-vip.yaml, \
    /usr/bin/cat /etc/rancher/rke2/rke2.yaml
Cmnd_Alias RKE2_CHECKS = \
    /var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml *, \
    /usr/bin/rke2 secrets-encrypt status, \
    /usr/bin/rke2 etcd-snapshot list, /usr/bin/rke2 etcd-snapshot save, \
    /usr/bin/wg show, \
    /var/lib/rancher/rke2/bin/crictl --config /var/lib/rancher/rke2/agent/etc/crictl.yaml ps, \
    /var/lib/rancher/rke2/bin/crictl --config /var/lib/rancher/rke2/agent/etc/crictl.yaml pods, \
    /var/lib/rancher/rke2/bin/crictl --config /var/lib/rancher/rke2/agent/etc/crictl.yaml inspect *, \
    /var/lib/rancher/rke2/bin/crictl --config /var/lib/rancher/rke2/agent/etc/crictl.yaml logs *

%<middleware-group> ALL = (root) RKE2_UNITS, RKE2_LOGS, RKE2_FILES, RKE2_CHECKS
```

A few things about it:

- **Logs: better the `systemd-journal` group than `sudo`** (`adm` on Ubuntu). It reads every log, changes nothing, and
  then `journalctl` takes any options without `sudo`. Through `sudo`, the rule lists exact forms because a wildcard
  after them would also allow `--vacuum-time`, which deletes logs.
- **The pager.** `journalctl` shows long output in a pager (`less`), and from `less` run as root, typing `!` opens a
  root shell. systemd 247 and later (RHEL 9, Ubuntu 22.04 and 24.04) runs the pager in a safe mode under `sudo`. On
  older systemd (RHEL 8's is 239), add `--no-pager` to each `journalctl` form in the rule.
- **`systemctl status rke2-server`** and `systemctl is-enabled rke2-drain` need no `sudo`: anyone can run them.
- **`crictl`** only lists and reads (`ps`, `pods`, `inspect`, `logs`): `crictl exec` runs a command in any container on
  the server, including privileged ones, which is root.
- **`kubectl … *`** is the cluster's administrator, on purpose: it's what the kubeconfig is for. Once the kubeconfig is
  on administrators' machines, this line can go.
- **No `ALL`, and no `NOPASSWD`.** People type their own password, so `sudo` logs who ran what. Send those logs
  (`/var/log/secure` or `/var/log/auth.log`) to the campus SIEM like the rest.
- **For an install or upgrade window,** add a second file that gives full `sudo` (`%<middleware-group> ALL = (ALL)
  ALL`), and remove it after.

## Checklist

On the [checklists page](checklist.md#servers): the middleware group's `sudo` rule in place on every server.
