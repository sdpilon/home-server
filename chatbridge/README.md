# chatbridge (deployment)

This directory is the **deployment stack** for chatbridge, not the source.

The project — bridge code, the `homeops` agent definition, Dockerfile, tests and
docs — lives in its own repository at **`/home/sp/chatbridge`**, and the image is
built from there:

```bash
cd /app/data/projects/chatbridge
docker compose build          # builds from /home/sp/chatbridge
docker compose up -d
```

What lives here:

| Path | Purpose |
| --- | --- |
| `compose.yaml` | the stack definition (host networking, mounts, env) |
| `.env` | your configuration: senders, model, bridge behaviour (untracked) |
| `.env.example` | template for the above |
| `./data` | the Delta Chat account and the chat→session map (untracked) |
| `agent` | symlink to the project's agent directory, kept so OpenCode sessions created before the move still resolve their agent definition |

`./data` is the bot's identity: it holds the account's private keys. Back it up or
lose the address.

Setup, usage and troubleshooting are documented in the project:
`/home/sp/chatbridge/README.md`, `SETUP.md` and `DELTA-CHAT.md`.
