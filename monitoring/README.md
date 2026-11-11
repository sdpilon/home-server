# Monitoring

Prometheus + Grafana for the homelab, alongside the existing Gatus/ntfy stack.

## URLs

- Grafana: `https://grafana.${DOMAIN}`
- Gatus: `https://gatus.${DOMAIN}`
- ntfy: `https://ntfy.${DOMAIN}`
- Seed Priority (`qbit-priority`) UI: `https://qbit-priority.${DOMAIN}` (form login; see below)
- Prometheus: internal only (`http://prometheus:9090` on the `monitoring` network)

## Secrets

Stored in `.env` (gitignored):

- `GRAFANA_ADMIN_PASSWORD` — Grafana `admin` password
- `QBITTORRENT_API_KEY` — qBittorrent WebUI API key (`qbt_...`)

Rotate the Grafana password by changing `.env` and recreating the container:

```sh
docker compose up -d --force-recreate grafana
```

## Dashboards

Provisioned from `grafana/dashboards/` (read-only, `allowUiUpdates=false`):

- **qBittorrent** (`OEyH9tQZk`) — from the [martabal/qbittorrent-exporter](https://github.com/martabal/qbittorrent-exporter) project
- **Node Exporter Full** (`rYdddlPWk`) — host CPU, memory, disk, filesystem, NFS
- **qBittorrent / NAS Health** (`nas-health`) — custom view of disk queue, NFS
  throughput, iowait, and IO pressure; linked from the Grafana alerts
- **NAS Host** (`nas-host`) — ZimaBlade CPU, memory, load, HDD/eMMC/SSD space,
  HDD I/O, SMART health/temperature/attributes, and NFS throughput

To customise a dashboard, edit the JSON here; Grafana's file provisioner picks
it up within 30 seconds (or set `allowUiUpdates: true` in
`grafana/provisioning/dashboards/default.yml`).

## What is scraped

| Job | Target | Metrics |
|---|---|---|
| `prometheus` | `localhost:9090` | self |
| `node` | `node-exporter:9100` | host CPU, memory, disk, filesystem, NFS/mountstats |
| `qbittorrent` | `qbittorrent-exporter:8090` | speeds, torrents, peers, queue, buffers |
| `nas` | `172.16.1.3:9100` | ZimaBlade CPU, memory, disk, filesystem, btrfs, NFS server |
| `nas-smart` | `172.16.1.3:9633` | HDD SMART health, temperature, and attributes |

`qbittorrent-exporter` joins `arr_network` so it can reach the qBittorrent
WebUI at `gluetun:8080` (qBittorrent shares gluetun's network namespace).

The NAS exporters run on `zimbian` as Debian host services (`node-exporter`,
configured via `/etc/default/prometheus-node-exporter`, and the SMART exporter on
port 9633). They listen only on the LAN and are scraped by the Prometheus
container; no router ports are forwarded for them.

## Alerts

Provisioned in `grafana/provisioning/alerting/`:

- **Contact points** (custom webhook payloads → ntfy):
  - `ntfy-routine` → topic `homelab-routine`, priority 3
  - `ntfy-critical` → topic `homelab-critical`, priority 5
- **Policy**: default route is `ntfy-routine`; `severity=critical` routes to `ntfy-critical`.
- **Rules** (folder `Homelab`, group `qBittorrent / NAS`, evaluated every 1m):

| Rule | Expression | For | Severity |
|---|---|---|---|
| qBittorrent disk queue high | `avg_over_time(qbittorrent_global_average_time_queue[5m]) > 55000` | 10m | warning |
| qBittorrent disk queue critical | same `> 90000` | 5m | critical |
| qBittorrent IO stalled high | `rate(node_pressure_io_stalled_seconds_total[5m]) * 100 > 60` | 15m | warning |
| Host iowait high | `avg(rate(node_cpu_seconds_total{mode="iowait"}[5m])) * 100 > 60` | 15m | warning |
| /data free space low | `node_filesystem_avail_bytes{mountpoint="/data"} < 200 GB` | 5m | critical |
| qBittorrent exporter down | `up{job="qbittorrent"} < 1` | 5m | critical |
| Node exporter down | `up{job="node"} < 1` | 5m | critical |

- **Rules** (folder `Homelab`, group `NAS host`, evaluated every 1m):

| Rule | Expression | For | Severity |
|---|---|---|---|
| NAS node exporter down | `up{job="nas"} < 1` | 5m | critical |
| NAS SMART exporter down | `up{job="nas-smart"} < 1` | 5m | critical |
| NAS SMART health failed | `smartctl_device_smart_status{...} < 1` | 5m | critical |
| NAS HDD reallocated sectors | `smartctl_device_attribute{attribute_name="Reallocated_Sector_Ct",attribute_value_type="raw"} > 0` | 5m | critical |
| NAS HDD pending sectors | `smartctl_device_attribute{attribute_name="Current_Pending_Sector",attribute_value_type="raw"} > 0` | 5m | critical |
| NAS HDD offline uncorrectable | `smartctl_device_attribute{attribute_name="Offline_Uncorrectable",attribute_value_type="raw"} > 0` | 5m | critical |
| NAS HDD CRC errors increasing | `delta(UDMA_CRC_Error_Count[6h]) > 0` | 10m | warning |
| NAS HDD temperature high | `smartctl_device_temperature > 50` | 10m | warning |
| NAS HDD space low | HDD-Storage used `> 85%` | 10m | warning |
| NAS btrfs device errors | `increase(node_btrfs_device_errors_total[10m]) > 0` | 5m | warning |

Resolved notifications are enabled. To tune a threshold, edit `rules.yaml` and reload:

```sh
source .env
curl -sk -u "admin:$GRAFANA_ADMIN_PASSWORD" -X POST \
  https://grafana.${DOMAIN}/api/admin/provisioning/alerting/reload
```

## Baselines

Measured qBittorrent/disk baselines are archived in [`baselines/`](baselines/README.md):

| Run | What | Result |
|---|---|---|
| [`2026-10-23-qbt-no-limits-3h`](baselines/2026-10-23-qbt-no-limits-3h/) | 3 h, queueing disabled, 80+ seeds, no downloads | queue 32.4 s avg, iowait 25.0%, NFS reads 5.75 MB/s |
| [`2026-10-23-qbt-seed-ramp`](baselines/2026-10-23-qbt-seed-ramp/) | 20/35/50/65/82 active seeds, default per-torrent limits | **20 is the sweet spot**; 35 is the upper edge |
| [`2026-10-23-qbt-conn-tuning`](baselines/2026-10-23-qbt-conn-tuning/) | 50 active, 20 connections / 2 upload slots per torrent | iowait 17.3%, IO-full 19.3% — **50 is comfortable** |

Key takeaway: the SMR drive is random-read latency-bound, not bandwidth-bound.
Raising `max_active_torrents` alone (default per-torrent limits) increases
iowait and IO pressure while lowering upload throughput and peer count. Capping
per-torrent fan-out to 20 connections / 2 upload slots fixes that: 50 active
seeds then costs about the same iowait and IO pressure as 20 active did, for
~10% less upload. The 3 h run was seeding-only, so it says nothing about
download-write performance.

Current qBittorrent tuning:

| Setting | Value |
|---|---|
| `max_active_torrents` | 50 active, 32 stopped (82 total) |
| `max_active_downloads` | 3 |
| `max_active_uploads` | 20 |
| `max_connec` | 300 |
| `max_connec_per_torrent` | 20 |
| `max_uploads` | 20 |
| `max_uploads_per_torrent` | 2 |
| `dl_limit` / `up_limit` | 20 MiB/s / 50 MiB/s |

Seed priority: the `servarr/qbit-priority` sidecar keeps 50 seeds active and the
other 32 stopped. **Unsatisfied** seeds fill slots first, **Satisfied** seeds
fill what remains and are ranked by lifetime average upload rate, and
**Optional** seeds only run in otherwise unused slots. Priority is enforced
every 60 seconds; FIFO rotation inside an over-capacity Unsatisfied/Optional
tier uses the configured interval and swap count. Inactive seeds are kept
stopped rather than queued because qBittorrent's demand-based seed queue
otherwise swaps its own choices back in. See
[`../servarr/qbit-priority/README.md`](../servarr/qbit-priority/README.md).

The controller has a web UI at `https://qbit-priority.${DOMAIN}` with its own
form login (session cookie, not a browser basic-auth popup). It uses a
full-width application shell with a collapsible control sidebar, a dense
sortable torrent table, and a status bar. It controls pause/resume, interval,
swap count, run now, preview, and per-torrent priority. The torrent list has
separate priority and state filters; on narrow screens it switches to labeled
cards. Metrics, controls, and table columns have hover/focus tooltips
explaining them. Public `/healthz` is checked by Gatus and used by the Dashlit
bookmark.

Each directory holds `samples.csv`, `summary.txt`, and the before/after
qBittorrent settings. The same metrics are still in Prometheus (30 day
retention) and visible in the **NAS Health** dashboard.

## Retention

Prometheus keeps 30 days or 2 GB, whichever comes first. Data lives on
`/docker/appdata/monitoring/prometheus`; Grafana data on
`/docker/appdata/monitoring/grafana`.
