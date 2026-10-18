# Setup, step by step

The long-form companion to the setup section of [README.md](README.md): what each
command actually *does*, where its state goes, and what to do when it doesn't
work. If you only want the short version, the README has it.

If Delta Chat itself is new to you, read [DELTA-CHAT.md](DELTA-CHAT.md) first — it
is the background this document assumes (what a relay is, why there is no
password, and what the invite link is actually for).

## The moving parts

| Where | What |
| --- | --- |
| container `chatbridge` | Python 3.13 + `deltabot-cli` 9.1.0 + `deltachat2` 2.58.0, with the `deltachat-rpc-server` binary (Delta Chat's Rust core). Holds the bot's Delta Chat account and speaks HTTP to OpenCode. |
| `./data` on the host | Everything the container persists: the Delta Chat account and the bridge's chat→session table. Bind-mounted at `/data`. |
| the host's OpenCode service | `127.0.0.1:49374`. Runs the agent and executes the commands. Reads the agent definition from `agent/.opencode/agents/homeops.md`. |

`network_mode: host` is not a preference: OpenCode binds to loopback only, so the
container has to share the host's network namespace to reach it. It publishes no
ports of its own.

Every command below runs the same image with a different subcommand. What ties
them together is the entrypoint:

```bash
exec python -m bridge --config-dir /data/dc --logging info "$@"
```

`python -m bridge` → `bridge/__main__.py` → `cli.start()`, which parses `argv` and
dispatches. `--config-dir /data/dc` is why the account ends up in `./data/dc` on
the host instead of inside the container's own filesystem, and why `init`, `link`
and `serve` all agree on which account they are talking about.

## 1. Build

```bash
docker compose build
```

Produces `chatbridge:local`. The only thing worth knowing is which edits need a
rebuild:

| Edited | Rebuild? |
| --- | --- |
| `bridge/*.py`, `Dockerfile`, `requirements.txt` | **yes** — `docker compose build && docker compose up -d --force-recreate` |
| `agent/.opencode/agents/homeops.md` | no — the host's OpenCode reads it directly, once per new session |
| `.env`, `compose.yaml` | no — `docker compose up -d` recreates the container and applies them |

## 2. Create the bot's Delta Chat account

```bash
docker compose run --rm chatbridge init DCACCOUNT:nine.testrun.org
# or, with a mailbox you already own:
docker compose run --rm chatbridge init bot@example.com 'app-password'
```

Reading that from the inside out:

- `docker compose run` starts a **one-off** container from the same image, with
  the same `.env`, the same `./data` mount and the same host networking. It is
  not the service: it runs your command instead of `serve`, then exits, and `--rm`
  deletes it afterwards. It gets a generated name
  (`chatbridge-chatbridge-run-1a2b3c`), so it does not collide with the
  long-running `chatbridge` container.
- The entrypoint turns it into
  `python -m bridge --config-dir /data/dc --logging info init …`.
- `init` adds a new account profile under `/data/dc`, marks it as a bot
  (`set_config(accid, "bot", "1")`), and then hands your argument to Delta Chat's
  own configuration machinery — which is what actually logs in.

The two forms differ only in that last step:

**`DCACCOUNT:<host>` — a chatmail account, no mailbox needed.** Delta Chat's core
contacts the chatmail server named after the colon; the server creates a mailbox
for the bot on the spot and returns its credentials, which the core stores
locally. That is why there is no password to supply and nothing to set up in a
mail client — the account is created for the bot and exists only in `./data/dc`.

**`<address> <password>` — an existing mailbox.** Use an app password. Normal
IMAP/SMTP autoconfig runs against the address's domain, and whatever the server
returns is written into the account profile.

Both forms print a live progress bar driven by Delta Chat's `configure_progress`
events, and end in either `Account configured successfully.` or
`Configuration failed.` with exit code 1. A failure here is nearly always a wrong
password, a normal password where an app password was required (many providers
want 2FA enabled first), or an unreachable server. A mistyped chatmail host looks
like this and leaves the half-made profile behind:

```
ERROR  {'code': -1, 'message': 'Error:\n\n"IMAP failed to connect to
        imap.no-such-chatmail.invalid:993:tls: Could not find DNS resolutions
        for imap.no-such-chatmail.invalid:993. Check server hostname and your
        network"'}
Configuring... ━━━━━━━━━━━━━━━━━━━━━╸  91% 0:00:01
ERROR  Configuration failed.
```

What lands on disk:

```
data/dc/accounts/
├── accounts.toml                 # registry: selected_account, next_id, accounts_order, [[accounts]] id/dir/uuid
├── accounts.lock
└── 6985fc15-…-a3838c053e06/      # one directory per account, named by its uuid
    ├── dc.db                     # the Delta Chat database: this *is* the account
    └── dc.db-blobs/              # attachments and key material
```

Back up `./data` and you have backed up the bot's identity. Lose it and the
account is gone — recovery would mean running `init` again and getting a
different address.

`init` is a one-time step. Running it again creates a *second* profile (ids
increment); to replace an account, create the new one and remove the old — see
"Changing the bot's account" below.

## 3. Get the invite link

```bash
docker compose run --rm chatbridge list    # which accounts exist, and their addresses
docker compose run --rm chatbridge link    # prints "Account #1:" then the invite
```

`list` prints `#<id> - <address>` for each account, or `(not configured)` if
setup never completed.

`link` prints a Delta Chat **Secure-Join** invite: an `https://i.delta.chat/#…` URL
carrying the bot's address *and* a fingerprint of its encryption key. The
fingerprint is what makes it trustworthy — a relay that tried to substitute its own
key would not match it. The invite is single-use, and completing the handshake also
auto-accepts your contact request, so opening it lands you in a working chat rather
than a pending request.

Two things worth knowing:

- The invite is a convenience, not a credential. Anyone holding it can *open a
  chat* with the bot, but the bot still refuses anyone not in `ALLOWED_SENDERS`.
  Being able to add the contact and being allowed to use it are separate.
- Don't confuse it with `docker compose run --rm chatbridge admin`, which prints
  the invite to the bot's *admin group*. That one is dangerous — its own help text
  warns that anyone who joins becomes an admin of the bot. Never share it.

## 4. Start serving

```bash
docker compose up -d
docker compose logs -f chatbridge
```

`serve` is the container's default command. On startup it:

1. asks the Delta Chat core for all accounts and checks whether each is
   configured;
2. logs `Listening at: <invite link>` for the good ones, `account N not
   configured` for the others;
3. gives up with `There are no configured accounts to serve` and exit 1 if none
   qualify;
4. only then runs this stack's startup hook, which loads `.env`, reads the
   OpenCode password from the mounted `service.json`, calls OpenCode's health
   endpoint (refusing to start, with an explanation, if it is unreachable), warns
   if `ALLOWED_SENDERS` is empty, sets the account's display name to
   `Home Server`, and finally
5. enters the message loop.

**Starting it before creating an account** gives you exactly that step-3 error,
and since the service is `restart: unless-stopped`, it retries forever:

```
ERROR  There are no configured accounts to serve
```

Alarming to watch, but harmless and self-healing: each restart re-reads
`./data/dc`, so as soon as `init` has produced a configured account the loop
becomes a working bot on its own. You can watch the message change from *"There
are no configured accounts to serve"* to *"account 1 not configured"* the moment
an incomplete profile appears — proof that it is re-reading the directory. If you
would rather not see the noise, do step 2 first.

## 5. Let yourself in

`ALLOWED_SENDERS` starts empty, and an empty list means **refuse everyone**. That
is deliberate: a bot with docker access should not be open during the seconds
between first start and configuration. So the first message you send gets:

```
Sorry, this bot only serves its owner.

Your address is:
you@example.com

(Add it to ALLOWED_SENDERS in .env to allow it.)
```

Paste that address into `.env`:

```dotenv
ALLOWED_SENDERS=you@example.com
```

and apply it:

```bash
docker compose up -d
```

`up -d` and not `restart`: environment comes from `.env` via `env_file`, and only
recreating the container picks up a change. `docker compose restart` reuses the
existing container and would silently keep the old value. Confirm with `/whoami`
in the chat.

Comma-separate several addresses for a second phone or a second person; each chat
gets its own session and its own context.

## Day-2

**Changing behaviour** — edit `.env` (`OPENCODE_MODEL`, `BRIDGE_ACK_AFTER`, …),
then `docker compose up -d`.

**Upgrading the code** — `docker compose build && docker compose up -d
--force-recreate`.

**Rotating the OpenCode password** — nothing to change here: `service.json` is
bind-mounted, so `docker compose restart chatbridge` is enough for the container
to re-read it.

**Stopping** — `docker compose down` keeps `./data`, and therefore the account.
Start it again with `docker compose up -d`.

**Starting over** — `docker compose down && rm -rf data` deletes the account.
You will have to `init` again, and the bot's address will change.

**Changing the bot's account** — create the new one, then remove the old. Removal
needs to know which account, and requires `-a` once more than one exists:

```bash
docker compose run --rm chatbridge init bot@example.com 'new-app-password'
docker compose run --rm chatbridge list
docker compose run --rm chatbridge -a 1 remove      # remove account #1
docker compose run --rm chatbridge -a 2 link        # invite for the new one
```

**Backup and restore** — `export` writes a Delta Chat backup into a directory
that already exists; `import` restores one from a backup file. Both paths are
inside the container, so mount the folder you want to use:

```bash
docker compose run --rm -v "$PWD/backup:/backup" chatbridge export /backup
docker compose run --rm -v "$PWD/backup:/backup" chatbridge import /backup/<backup-file>
```

**Inspecting the account** — `config` reads and writes Delta Chat account
settings, for example `docker compose run --rm chatbridge config displayname`.

## Troubleshooting

| Symptom | Cause, and what to do |
| --- | --- |
| `There are no configured accounts to serve` in a loop | No account yet. Run the `init` step; the loop resolves itself. |
| `account 1 not configured` | `init` made the profile but login failed. Re-run `init`, removing the half-made profile first if it objects (`-a 1 remove`). |
| `Configuration failed.` during `init` | Wrong password, or a normal password where an app password is required. Some providers need 2FA enabled before app passwords can be created. |
| The bot replies with your address | You are not in `ALLOWED_SENDERS`, or you edited `.env` without recreating the container (`up -d`, not `restart`). |
| `cannot reach the OpenCode server … Connection refused` | OpenCode's background service is not running on the host, or the container lost host networking. |
| `cannot read OPENCODE_PASSWORD_FILE …` | The `service.json` bind mount is missing; check the path in `compose.yaml` against the real home directory. |
| `OPENCODE_SESSION_DIR … does not exist` | The `agent/` directory moved. Keep it in place, or update the variable. |
| Answers arrive but no command ever runs | Every non-read-only command needs a `yes` in chat. That prompt is the feature, not a failure. |
| A run ends with *"Gave up after Ns"* | Raise `BRIDGE_TIMEOUT`, or send `/stop` and ask again. |
| The bot is slow to answer | The first message of a session pays for the model's cold start; later ones are quicker. `/new` starts a session and pays it again. |

Useful while something is off:

```bash
docker compose logs -f chatbridge                          # what the bot is doing
docker compose run --rm chatbridge --logging debug serve   # foreground, verbose
docker compose run --rm chatbridge list                    # is the account really configured?
docker compose run --rm chatbridge config                  # the account's Delta Chat settings
```

Anyone who can run `docker` on this host can read `./data`, which holds the bot's
private keys in `dc.db`. That is the same trust level as the rest of the Docker
setup here; worth knowing, but it grants nothing new.
