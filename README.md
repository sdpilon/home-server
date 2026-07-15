# Docker VM Home Server Configuration

My personal Docker stacks setup. Not intended for public use.


## Overview

- Running on a Debian 13 (bookworm) VM in Proxmox
- Accesses storage over NFS mounts on my NAS
- Uses [Arcane](https://getarcane.app/) for Docker Compose management.
  - It's actually useful on the scale I'm currently working at, i.e., a single environment.
  - It has the right level of control for me, where I can directly edit the `compose.yaml`s (or any files now after a recent update).
  - The right amount of monitoring and visibility.
  - And a nice looking UI which provides most functionality you need without having to ssh in for everything, which is handy even when I'm away from the computer, because the mobile version is fully usable. I have spun up a new stack from my phone while laying in bed.
- Even though I use Arcane, everything still works without using Arcane, so I can still manage my stacks from the command line.

## Stacks & Services:


- `arcane` -- The arcane web UI for managing Docker Compose stacks.
  - 2 containers: arcane, docker socket proxy

- `bentopdf` -- Tools for working with PDF documents, but local. I mainly use it for splitting and merging. Smaller docker image size compared to sterlingPDF.
  - 1 container: bentopdf

- `immich` -- The main photo management app I use every day.
  - 4 containers: immich app, immich Machine learning backend, postgres database, redis cache

- `it-tools` -- Some handy IT-related utilities. So I don't have to google to find some specific tool, find random website, and then enter my data into said random website.
  - 1 container: it-tools

- `jellyfin` -- Media server, for serving media.
  - 2 containers: jellyfin app, tizen tv app installer/updater (only for docker profile: tv-app)

- `ntfy` --  Currently not actually being used for anything.
  - 1 container: ntfy

- `omnitools` -- Same story as it-tools, but more general purpose.
  - 1 container: omnitools

- `servarr` -- My media management and torrenting stack.
  - 8 containers: sonarr, radarr, lidarr, bazarr, qbittorrent, prowlarr, gluetun, qb port updater

- `traefik` -- Reverse proxy and SSL termination for my services. Moved from `nginx-proxy-manager` because the config is declarative and lives in each service's compose file, for containers anyway. Some are outside of this VM, and are configured separately.
  - 1 container: traefik

- `uptime-kuma` -- Monitoring my containers, and other services on other hosts, as well as the proxied URLs.
  - 1 container: uptime-kuma

## Setup

Clone the repo to the directory where you want your Compose projects to live.

For each service, copy the `*.example` file(s) and remove the `.example` suffix, then fill in the values for the environment.

For example, to set up `immich`,
- copy `immich/.env.example` to `immich/.env` and fill in the values for the environment
- copy `immich/secrets/postgres_username.txt.example` to `immich/secrets/postgres_username.txt` and fill in the real username
- copy `immich/secrets/postgres_password.txt.example` to `immich/secrets/postgres_password.txt` and fill in the real password
- run `docker compose up -d`
