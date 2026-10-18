# Delta Chat and chatmail, briefly

Enough background that every step in [SETUP.md](SETUP.md) makes sense if you have
never used Delta Chat. It is a concepts doc, not a manual; the links at the end go
deeper.

## The short version

Delta Chat is an instant messenger, and underneath it sends **email**. There is no
Delta Chat network, no Delta Chat company, and no account to register: a Delta
Chat address *is* an email address, and messages travel over the same IMAP/SMTP
plumbing as any other mail.

What makes that pleasant rather than absurd is the other half of the design: the
mail server is normally a **chatmail relay**, a deliberately minimal server that
only relays end-to-end encrypted messages, does not archive them, and does not
store your contacts or groups. Relays hand out addresses from inside the app, with
no password and no mailbox to administer.

So, in one line each:

- *Creating the bot's account* = getting it an address on such a relay.
- *The bot* = an ordinary Delta Chat profile, driven by a program instead of a
  phone UI.
- *The invite link* = the thing that teaches your phone which key belongs to that
  address, without trusting any server.

## Addresses are email addresses

A Delta Chat address looks like `someone@nine.testrun.org`. Two consequences worth
internalising:

- **It is interoperable.** Delta Chat is not a walled garden, and relays are not
  one operator's service — they federate with each other.
- **It is not tied to you.** No phone number, no email verification, no public
  directory. On a chatmail relay the address is generated for you, and you can
  have several profiles, each with its own address and its own keys.

A **profile** is a Delta Chat identity: an address, a private key, and the chats on
it. Profiles live on your device, not on the server. That matters for this stack,
because "the device" here is `./data` — the container's Delta Chat profile is a
directory of files on your host, and the server only ever relays messages for it.
The FAQ puts it as: relays "do not store any user data as group states, your name
or avatar — all those exist only on your device".

## Relays, and what chatmail adds

A **relay** is just a mail server. Delta Chat works with any mail server, but the
[chatmail](https://chatmail.at) project defines the kind Delta Chat is designed
around, and those servers enforce things that ordinary mail servers don't:

- **Encryption is mandatory.** Only strict TLS connections are accepted, messages
  must be DKIM-signed and OpenPGP-encrypted, and unencrypted messages are rejected
  outright. With a chatmail relay it is impossible to send or receive without
  end-to-end encryption.
- **Messages are ephemeral.** A relay holds a message only long enough to hand it
  to the recipient, then deletes it. It is transport, not an archive.
- **Metadata is minimised.** Addresses on chatmail relays are randomly generated,
  and contact and group metadata lives in the encrypted part of messages, so a
  relay sees essentially sender, recipient and size.
- **No spam machinery.** Because only encrypted mail is accepted, relays don't
  need IP reputation or spam filtering — which is why they are cheap to run and
  why apps and bots can create addresses freely.

Public relays are run by different groups and communities; [nine.testrun.org](https://nine.testrun.org)
is the default onboarding relay for many chatmail apps. There are [many
others](https://chatmail.at/relays), in various countries, and they all
interoperate.

This is the whole reason `init` has two forms:

- **`init DCACCOUNT:nine.testrun.org`** — ask that relay to create an address on
  the spot. The server returns the credentials, Delta Chat stores them locally, and
  there is no password to type and no mailbox to log into. This is a chatmail
  account.
- **`init you@example.com 'app-password'`** — use a mailbox you already have as the
  relay instead, with an app password. Ordinary IMAP/SMTP autoconfig runs against
  the domain.

The second form works, but it is the non-native path. A classic provider will not
enforce encryption for you, so messages *can* be unencrypted, and Delta Chat's own
documentation asks that you only use a classic address exclusively with chatmail
apps — sharing a mailbox with a normal mail client means encrypted blobs in your
inbox and double notifications. If you have no reason to prefer it, use the
chatmail form.

## Encryption, and what the invite link really is

Encryption in Delta Chat is OpenPGP with two layers of automation:

- **Autocrypt** is how keys usually reach each other: a public key travels along
  with the messages themselves. No key servers, no web of trust, no fingerprints to
  compare by hand.
- **Secure-Join** is the stronger path, and it is what the invite link and the QR
  code are. The invite carries the address **plus a fingerprint of the key plus
  two one-time challenges**, and it reaches you *out of band* — as a link you tap
  or a QR code you scan, not through the mail server. A server that tried to
  substitute its own key would be caught, because the substituted key would not
  match the fingerprint from the out-of-band channel.

The link is roughly:

```
https://i.delta.chat/#<key-fingerprint>&a=<address>&n=<name>&i=<invite>&s=<auth>
```

Only the shape matters. Each invite is single-use, and completing the handshake is
what gives both sides certainty about the other's key. A useful side effect: a
Secure-Join invite causes the recipient's contact request to be accepted
automatically, which is why scanning the bot's invite link gets you a working chat
rather than a pending request.

Two current-state notes, since older write-ups differ:

- Since Delta Chat's **v2** release series (July 2025) contacts are identified by
  their cryptographic key, and the old green "verified" checkmark is gone — it
  became redundant. The relay operator structurally cannot swap in a key, because
  it never sees one. So don't go looking for a badge: a chat with the bot is
  already the strong case.
- There is **no forward secrecy yet**. Autocrypt v2, which will bring it along with
  post-quantum resistance, is scheduled for full implementation in 2026. In
  practice: whoever holds the profile's private key can decrypt messages that were
  already collected. That is worth remembering when you think about backing up
  `./data`.

## Contact requests, and why access control exists anyway

Delta Chat has no public discovery — you decide who can reach you, and there is no
way to look up an address. But anyone who *has* the address, or who is handed your
invite link, can send you a first message.

A first message from an unknown address does not appear as a normal chat. It
arrives as a **contact request** that the recipient has to accept before the
conversation works. This is why the bridge calls Delta Chat's `accept_chat` on new
chats.

That mechanism is about *allowing the chat to exist*, not about *who may use the
bot* — and it stops mattering the moment the invite link floats around. So the
stack adds its own `ALLOWED_SENDERS` on top, and starts out refusing everybody.
Holding the invite link gets you a chat; it does not get you an answer.

## What a "bot" is in this picture

Nothing special, which is the point. A Delta Chat bot is a program that drives an
ordinary Delta Chat profile through Delta Chat's API instead of a touchscreen.
From your phone it is just another contact, with a name and an address.

The container holds three layers that turn "speaks IMAP/SMTP/OpenPGP" into
something Python can script:

| Layer | Role |
| --- | --- |
| `deltachat-rpc-server` | Delta Chat's Rust core, exposing the whole messenger as JSON-RPC over stdio. This is the part that actually talks to relays and does the encryption. It is the same core the phone apps use. |
| `deltachat2` | Python bindings that talk to that server. |
| `deltabot-cli` | The command-line scaffolding — `init`, `serve`, `list`, `link`, `admin` — plus the event hooks that deliver incoming messages to your code. |

`bridge/bot.py` plugs into the last layer: it receives messages, maps each chat to
an OpenCode session, and turns the agent's approval requests into yes/no prompts in
the chat. The `init` command also marks the profile as a bot
(`set_config(accid, "bot", "1")`), which is how Delta Chat labels it as
program-driven rather than a person.

## Reading SETUP.md with this in mind

| Setup step | What it actually is |
| --- | --- |
| `init DCACCOUNT:nine.testrun.org` | Ask a chatmail relay for a new address; the credentials land in `./data/dc`. |
| `init you@example.com 'app-pw'` | Use a mailbox you own as the relay instead of a chatmail one. |
| `list` | Which profiles exist in `./data`, and which address each answers on. |
| `link` | Emit the Secure-Join invite, so your phone learns the bot's key out of band. |
| `up -d` | Run the program that drives that profile and reaches OpenCode. |
| `ALLOWED_SENDERS` | The app-level allowlist, on top of Delta Chat's contact requests. |
| `./data` | The profile itself: address, private keys, chats — plus the bridge's session map. |
| `./data` in a backup | The bot's entire identity. Restore it and the account is the same; lose it and the address is gone. |

## Glossary

- **relay** — the mail server that holds your address and forwards messages.
  Transport only; chatmail relays keep nothing.
- **chatmail** — the project defining minimal, encryption-enforcing relays
  (and, since the core was renamed, the home of the Delta Chat core library).
- **profile** — a device-local Delta Chat identity: address, keys, chats.
- **contact request** — a first message from an unknown address; must be accepted
  before the chat works.
- **Autocrypt** — automatic OpenPGP key exchange carried in the messages
  themselves. No key servers.
- **Secure-Join** — the invite-link/QR handshake that verifies keys over an
  out-of-band channel. Produces the link that `link` prints.
- **DCACCOUNT** — the URI form that tells Delta Chat to ask a relay for a fresh
  address.
- **bot** — a program driving a profile through the API rather than a UI.

## Where to read more

- [delta.chat/en/help](https://delta.chat/en/help) — the official FAQ; the
  encryption and relays sections are the relevant ones.
- [chatmail.at](https://chatmail.at) — the relay project, and
  [chatmail.at/relays](https://chatmail.at/relays) for the public list.
- [securejoin.delta.chat](https://securejoin.delta.chat) — the Secure-Join
  protocol, including why the invite carries a fingerprint.
- [github.com/chatmail/core](https://github.com/chatmail/core) — the core library,
  with `standards.md` listing the RFCs involved.
- [github.com/deltachat/interface](https://github.com/deltachat/interface/blob/main/uri-schemes.md)
  — the exact `i.delta.chat` invite-link format.
