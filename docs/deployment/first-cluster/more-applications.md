# More applications

[Deployment](../README.md) › [First cluster](README.md) › More applications

This cluster is built for one service, which suits a first cluster and a service that opens untrusted uploads. It
needn't stay that way. This page is for when other applications come along.

## One cluster or several

The usual practice is **one cluster per environment** (production, and non-production for trying things first),
**shared by many applications**, each in its own namespace. Every cluster is another set of control software, add-ons,
patching and upgrades to keep up with, so clusters are added only for a reason:

| Give an application its own cluster when | Otherwise |
|---|---|
| A regulator or auditor wants a hard boundary (FERPA or HR records, payment data) | Its own namespace on a shared cluster |
| It needs a different Kubernetes version or upgrade schedule | |
| Different owners or budgets that don't want to coordinate | |

**The usual next cluster is a non-production one**: new applications, RKE2 upgrades and risky changes are tried there
before production.

## An application's own space

> **Changed 22 October 2026:** this service's own namespace now denies ingress by default (#453).

Each application gets a namespace, and with it:

- **Access:** its team can manage its namespace and nothing else (a Role and RoleBinding, or the built-in `admin` role
  bound to the namespace). Only cluster administrators manage the cluster.
- **A resource quota and default limits**, so one application can't take all the CPU or memory.
- **Pod Security labels** (`pod-security.kubernetes.io/enforce: restricted` where the application allows it).
- **A default-deny network policy**, then its own rules for what may reach it and what it may reach. (This service ships
  its own, `default-deny-ingress` among them, since #453.)

## What applications share, and what they don't

| Shared, run by the cluster's administrators | Each application's own |
|---|---|
| The ingress controller and the floating address | Its namespace, secrets and service accounts |
| cert-manager and its issuers | Its databases (it runs its own, or uses a campus database) |
| KEDA, metrics-server, the storage classes | Its images and their registry access |
| Monitoring of the cluster itself | Its backups of its own data |

## Its own nodes

An application that opens untrusted files, uses a lot of memory, or holds data of a different classification can have
nodes kept for it: a **taint** keeps everything else off them, and a **label** and toleration let it on
([how](scaling.md#4-the-pdf-engine-on-its-own-nodes)). This service's PDF engine is the first candidate if the cluster
is shared.

## This service on a shared cluster

It already keeps to its own namespace (`pdf-remediation`): its network policies, secrets and storage are all there.
From the cluster it needs only an ingress controller, KEDA (in the namespace `keda`) and the two storage classes,
which a shared cluster provides anyway. So it can share a cluster later without changes, and other applications can
join this one. Check the [address ranges](network.md#addresses) leave room first.
