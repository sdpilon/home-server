# Docker VM Home Server Configuration

This is my personal Docker Compose setup: one directory per stack, running on a Debian 13 (trixie) VM in Proxmox.

> [!CAUTION]
> The setup shown in this repository isn't meant to be exposed to the public internet. Several services have no authentication of their own and rely entirely on the network being private (I deploy it behind Tailscale).


## Overview

- Debian 13 (trixie) VM on Proxmox, managed with Docker Compose.
- Storage: NFS mounts from my NAS.
- Ingress: Traefik on ports 80/443 with TLS via the Cloudflare DNS-01 resolver. App hostnames use `*.vm1.spcloud.space`, which resolve to the host's Tailscale IP, so the services are tailnet-only. Nothing is intentionally public.
- Uses [Arcane](https://getarcane.app/) for Docker Compose management.
  - It's actually useful on the scale I'm currently working at, i.e., a single environment.
  - It has the right level of control for me, where I can directly edit the `compose.yaml`s (or any files now after a recent update).
  - The right amount of monitoring and visibility.
  - And a nice looking UI which provides most functionality you need without having to ssh in for everything, which is handy even when I'm away from the computer, because the mobile version is fully usable. I have spun up a new stack from my phone while laying in bed.
- Even though I use Arcane, everything still works without using Arcane, so I can still manage my stacks from the command line.

## Stacks & Services:

12 stacks / 32 containers. (The Jellyfin Tizen installer only runs with the `tv-app` profile.)

- `arcane` -- The arcane web UI for managing Docker Compose stacks.
  - 2 containers: arcane, docker socket proxy

- `audiobookshelf` -- Audiobook and podcast server.
  - 1 container: audiobookshelf

- `bentopdf` -- Tools for working with PDF documents, but local. I mainly use it for splitting and merging. Smaller docker image size compared to Stirling PDF.
  - 1 container: bentopdf

- `chatbridge` -- Delta Chat bridge to the local OpenCode service. This directory is deployment-only; the source lives in its own repository (see `chatbridge/README.md`).
  - 1 container: chatbridge

- `dashlit` -- Self-hosted bookmark dashboard.
  - 1 container: dashlit

- `immich` -- The main photo management app I use every day.
  - 4 containers: immich app, immich Machine learning backend, postgres database, redis cache

- `it-tools` -- Some handy IT-related utilities. So I don't have to google to find some specific tool, find random website, and then enter my data into said random website.
  - 1 container: it-tools

- `jellyfin` -- Media server, for serving media.
  - 2 containers: jellyfin app, tizen tv app installer/updater (only for docker profile: tv-app)

- `monitoring` -- Prometheus/Grafana dashboards, ntfy push notifications, and Gatus uptime checks. Grafana dashboards, datasources, and alert rules are provisioned from `monitoring/grafana/`; uptime checks live in `monitoring/gatus/config.yaml`.
  - 6 containers: ntfy, gatus, prometheus, grafana, qbittorrent-exporter, node-exporter

- `omnitools` -- Same story as it-tools, but more general purpose.
  - 1 container: omnitools

- `servarr` -- My media management and torrenting stack: the VPN boundary, qBittorrent, the *arr apps, and the seed priority controller.
  - 12 containers: gluetun, qbittorrent, qb-port-sync, qbit-priority, mousehole, radarr, sonarr, lidarr, prowlarr, readarr, rreading-glasses, rreading-glasses-db

- `traefik` -- Reverse proxy and SSL termination for my services. Moved from `nginx-proxy-manager` because the config is declarative and lives in each service's compose file, for containers anyway. Some are outside of this VM, and are configured separately through the file provider (`traefik/data/dynamic/`).
  - 1 container: traefik

## Setup

Prerequisites: Docker with Compose v2, plus the external network every stack shares with Traefik:

```sh
docker network create proxy
```

Copy the `*.example` files for the stack you're setting up, remove the `.example` suffix, and fill in the values. Each stack reads its configuration from a gitignored `.env`:

```sh
cp <stack>/.env.example <stack>/.env
```

Some stacks need additional files:

- `immich/secrets/postgres_username.txt` and `postgres_password.txt`
- `traefik/cf_api_token.txt` (Cloudflare API token for ACME)
- `servarr/.env.*.secrets` (VPN, Mousehole, qbit-priority, rreading-glasses)
- `monitoring/.env` (Grafana admin password, qBittorrent API key)
- `chatbridge/.env` plus its `./data` Delta Chat account

For example, to set up `immich`:

```sh
cp immich/.env.example immich/.env
# fill in immich/.env
cp immich/secrets/postgres_username.txt.example immich/secrets/postgres_username.txt
cp immich/secrets/postgres_password.txt.example immich/secrets/postgres_password.txt
# fill in the real values
docker compose -f immich/compose.yaml config --quiet
docker compose -f immich/compose.yaml up -d
```

Start `traefik` before the stacks that rely on it for HTTPS.
