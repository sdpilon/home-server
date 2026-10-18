# chatbridge

Ask about this home server from your phone, over Delta Chat, without SSHing in.

```
  Delta Chat (your phone)  ⇄  bot's own Delta Chat account (e-mail, E2E)
          │
          ▼
  chatbridge container  ──HTTP──▶  OpenCode background service (127.0.0.1:49374)
          │                              │  agent + tools
          └──── reply ◀──────────────────┴──▶ docker / systemd / files on this VM
```

Each Delta Chat chat gets its own OpenCode session, so follow-up questions keep
their context. Anything that *changes* the server must be approved in the chat
first — reply `yes` to run it once, `no` to skip it.

## Requirements

- The OpenCode background service running on the host (it is: `opencode v2.0.32`
  on `127.0.0.1:49374`, credentials in `~/.config/opencode/service.json`).
- A Delta Chat account for the bot. Either a mailbox you own (address +
  app password) or, with no setup at all, a free account on a chatmail server.
- Docker with Compose (this repo's usual workflow).

## Setup

The short version is below. **[SETUP.md](SETUP.md)** is the long version: what
each command actually does, where its state lives, and what to do when it doesn't
work. If Delta Chat is new to you, **[DELTA-CHAT.md](DELTA-CHAT.md)** is the
background — what chatmail relays are, why there is no password to set up, and what
the invite link is for.

### 1. Build and check the configuration

```bash
cd /app/data/projects/chatbridge
cp .env.example .env      # already present, edit as needed
docker compose build
```

### 2. Create the bot's Delta Chat account (once)

Easiest — a free account on a chatmail server, no mailbox needed:

```bash
docker compose run --rm chatbridge init DCACCOUNT:nine.testrun.org
```

Or use your own mailbox with an app password:

```bash
docker compose run --rm chatbridge init bot@example.com 'app-specific-password'
```

Then print the bot's invite link and address:

```bash
docker compose run --rm chatbridge link
docker compose run --rm chatbridge list
```

### 3. Start it

```bash
docker compose up -d
docker compose logs -f chatbridge
```

### 4. Let yourself in

Open the invite link from step 2 in Delta Chat and send the bot any message.
Because `ALLOWED_SENDERS` is still empty, it replies with **your** address and
refuses you. Put that address in `.env`:

```dotenv
ALLOWED_SENDERS=you@example.com
```

then apply it:

```bash
docker compose up -d
```

That is the whole access-control list: only these addresses get answers. Add more
comma-separated addresses if a second device or person should be allowed.

## Using it

Just ask, in plain language:

> how are the containers doing?
> is anything unhealthy right now?
> how full is the disk?
> why did jellyfin restart?
> which container is using the most memory?

Commands:

| Command   | Effect                                              |
| --------- | --------------------------------------------------- |
| `/help`   | show the help text                                  |
| `/new`    | start a fresh conversation (forgets the context)     |
| `/status` | session id, agent, model, tokens, cost              |
| `/stop`   | cancel the run in progress                          |
| `/whoami` | show the Delta Chat address you are messaging from  |

Replies are plain text. Delta Chat does not render markdown at all — its parser
was removed in the 2.x series — so backticks, `**bold**`, `## headings` and pipe
tables would all arrive as literal characters. The bridge therefore flattens the
agent's markdown before sending, and tells the agent not to write any in the
first place. Headings lose their hashes, tables become one line per row, code
fences keep their content, and bullets stay as `-`. Single `*asterisks*` are left
alone deliberately: they are indistinguishable from a glob like `ls *foo*`, and
damaging a command you are being asked to approve would be worse.

## Approvals

The bundled `homeops` agent can inspect anything read-only without asking. When it
wants to run a command that changes something, you get:

```
🔐 The agent wants to run:

docker restart jellyfin

Reply *yes* to allow it once, or *no* to reject it.
```

- `yes` (or `y`, `ok`, `go ahead`, …) → runs it once. Nothing is ever
  permanently allowed, so every change is confirmed individually.
- `no` (or `n`, `stop`, `cancel`, …) → the action is dropped and the run ends.
- Anything else → the bot repeats what it is waiting for.

Read-only commands are pre-approved so ordinary questions stay snappy:

- `docker ps`, `inspect`, `logs`, `stats`, `images`, `system df`, `compose ps/logs/config`
- `df`, `du`, `free`, `uptime`, `lsblk`, `ps`, `ss`, `ip addr/route`, `tailscale status`
- `systemctl status/is-active/list-units`, `journalctl`
- `git status/log/diff/show`, `head`, `tail`, `grep`, `wc`, `sort`, `jq`, …

Always refused, even with a `yes`: `docker system prune`, `docker volume rm`,
`rm -rf /`, `mkfs`, `dd ... of=/dev/...`, and anything that reads a `.env` file.

## Configuration

Everything lives in `.env` (git-ignored):

| Variable           | Default                                  | Meaning                                        |
| ------------------ | ---------------------------------------- | ---------------------------------------------- |
| `ALLOWED_SENDERS`  | *(empty)*                                | comma-separated Delta Chat addresses           |
| `OPENCODE_MODEL`   | *(OpenCode default)*                     | model as `provider/model`                      |
| `OPENCODE_AGENT`   | `homeops`                                | agent definition to run                        |
| `BRIDGE_ACK_AFTER` | `12`                                     | seconds before the "still working" note        |
| `BRIDGE_SHOW_TOOLS`| `1`                                      | append the commands the agent ran              |
| `BRIDGE_TIMEOUT`   | `900`                                    | abandon a run after this many seconds          |
| `BRIDGE_LOG`       | `info`                                   | `debug`, `info`, `warning`, `error`            |

Set in `compose.yaml` rather than `.env`: `OPENCODE_URL`,
`OPENCODE_PASSWORD_FILE`, `OPENCODE_SESSION_DIR`, `DC_CONFIG_DIR`, `STATE_DB`.

`OPENCODE_PASSWORD_FILE` points at the host's `~/.config/opencode/service.json`,
mounted read-only, so rotating that password needs no change here.

The default model is whatever OpenCode uses (`opencode/nemotron-3.5-super-vl-free`
at the time of writing). For sharper answers on multi-step questions, point
`OPENCODE_MODEL` at a stronger model — e.g. `OPENCODE_MODEL=opencode/<model>`.

## Security notes

- The container uses `network_mode: host` because the OpenCode service binds to
  loopback only. It listens on nothing itself and is not exposed to Traefik.
- Access is by allow-listed Delta Chat address, and Delta Chat is end-to-end
  encrypted, so message contents do not pass through a third party.
- The OpenCode agent runs as the unprivileged user `sp` (uid 1000, in the
  `docker` group). It has **no sudo**, so it cannot change host-level things.
- The agent definition denies the edit/write tools and the interactive question
  tool outright: this bot reads and reports, it does not modify files.
- Approvals are always `once`. Choose "yes" as many times as you like; nothing
  is ever promoted to a standing rule.

## Troubleshooting

`error: There are no configured accounts to serve` — run the `init` step first.

`cannot reach the OpenCode server ... Connection refused` — the OpenCode service
is not running (`opencode service status`), or the container is not on host
networking.

`cannot read OPENCODE_PASSWORD_FILE` — the mount of
`~/.config/opencode/service.json` is missing; check the path in the host's home.

`OPENCODE_SESSION_DIR ... does not exist` — the agent directory was moved; keep
`agent/` where it is, or update `OPENCODE_SESSION_DIR` in `compose.yaml`.

A run says *"Gave up after Ns"* — raise `BRIDGE_TIMEOUT`, or `/stop` and ask again.

Debugging a run:

```bash
docker compose logs -f chatbridge
docker compose run --rm chatbridge --logging debug serve
```

Every command in the setup above, and the account lifecycle around them, is
explained in more detail in [SETUP.md](SETUP.md).

## Files

```
chatbridge/
├── compose.yaml                     # the stack (host networking, volumes)
├── Dockerfile / entrypoint.sh       # image; injects --config-dir
├── requirements.txt                 # deltabot-cli + deltachat2[full]
├── .env / .env.example              # configuration
├── README.md                        # overview and quick start
├── SETUP.md                         # every setup command explained in depth
├── DELTA-CHAT.md                    # the Delta Chat / chatmail background
├── bridge/
│   ├── bot.py                       # Delta Chat hooks, sessions, approvals
│   ├── opencode.py                  # the OpenCode HTTP API client
│   └── state.py                     # chat -> session mapping (SQLite)
├── agent/.opencode/agents/homeops.md# the agent: prompt + permission rules
└── data/                            # Delta Chat account + bridge state (git-ignored)
```

## Optional: more visibility

The agent deliberately runs unprivileged, which has two consequences worth
knowing. Both are optional host changes outside this stack:

- **Host logs.** `journalctl` only shows the bot's own user's messages. For
  system-wide logs, `sudo usermod -aG systemd-journal sp` and recreate the
  container. Container logs via `docker logs` already work.
- **Restarting services.** Restarting *containers* through Docker works (that is
  the `docker restart` approval above). Restarting *host* systemd units needs
  root; without a sudo rule the agent will say so and stop.
