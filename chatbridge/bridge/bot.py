"""Bridge: Delta Chat  <->  local OpenCode server.

An incoming Delta Chat message is turned into an OpenCode prompt. Each chat gets
its own session so follow-up questions keep context. When the agent wants to run
a command that needs approval, the request is forwarded into the chat and the
answer ("yes"/"no") is fed back to OpenCode.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from deltachat2 import Bot, MessageData, SpecialContactId, events
from deltabot_cli import BotCli

from .opencode import OpenCodeClient, OpenCodeError
from .state import State

cli = BotCli("chatbridge")

MAX_MSG_CHARS = 4000

HELP = """🤖 Home server assistant

Ask about this box in plain language, for example:
• how are the containers doing?
• is anything unhealthy right now?
• how full is the disk?
• why did jellyfin restart?
• what is eating memory?

I answer through OpenCode, which can inspect Docker and run read-only commands.
If a fix needs a command that changes something, I will ask you to approve it
first - reply "yes" to allow it once, or "no" to reject it.

Commands
/new      start a fresh conversation (clears context)
/status   show details of the current session
/stop     cancel the run in progress
/whoami   show the address you are messaging from
/help     this message"""

CONFIRM_RE = re.compile(
    r"^(y|yes|yeah|yep|yup|ok|okay|sure|go|go ahead|approve|approved|allow|"
    r"do it|run it|confirm|confirmed|proceed)\b",
    re.I,
)
REJECT_RE = re.compile(
    r"^(n|no|nope|nah|stop|cancel|don'?t|deny|denied|reject|rejected|abort|"
    r"nevermind|never mind|no thanks)\b",
    re.I,
)
KNOWN_COMMANDS = {"/help", "/start", "/new", "/status", "/stop", "/whoami"}

_log: Optional[object] = None
_bridge: Optional["Bridge"] = None


# --------------------------------------------------------------------- config


@dataclass
class Config:
    session_dir: str
    agent: str
    model: Optional[dict]
    allowed_senders: set[str]
    ack_after: float
    show_tools: bool
    timeout: float
    title_prefix: str


@dataclass
class Pending:
    request_id: str
    action: str
    resources: list
    asked_at: float
    reason: str = ""
    decision: Optional[str] = None  # "once" | "reject"
    replied: bool = False

    def describe(self) -> str:
        parts = [str(r) for r in self.resources] or [self.action]
        if len(parts) == 1:
            return parts[0].strip()
        # a compound command arrives as one resource per command
        return "\n".join(f"{i}. {p.strip()}" for i, p in enumerate(parts, 1))


def _load_config() -> Config:
    raw_senders = os.environ.get("ALLOWED_SENDERS", "")
    senders = {s.strip().lower() for s in raw_senders.split(",") if s.strip()}

    model: Optional[dict] = None
    raw_model = (os.environ.get("OPENCODE_MODEL") or "").strip()
    if raw_model:
        provider, _, model_id = raw_model.partition("/")
        if model_id:
            model = {"providerID": provider, "id": model_id}
        else:
            model = {"providerID": "opencode", "id": provider}

    return Config(
        session_dir=os.environ.get(
            "OPENCODE_SESSION_DIR", "/app/data/projects/chatbridge/agent"
        ),
        agent=os.environ.get("OPENCODE_AGENT", "homeops"),
        model=model,
        allowed_senders=senders,
        ack_after=float(os.environ.get("BRIDGE_ACK_AFTER", "12")),
        show_tools=os.environ.get("BRIDGE_SHOW_TOOLS", "1").lower()
        not in ("0", "false", "no", "off", ""),
        timeout=float(os.environ.get("BRIDGE_TIMEOUT", "900")),
        title_prefix=os.environ.get("BRIDGE_TITLE_PREFIX", "[chatbridge]"),
    )


def _load_password() -> str:
    password = os.environ.get("OPENCODE_PASSWORD")
    if password:
        return password.strip()
    path = os.environ.get("OPENCODE_PASSWORD_FILE")
    if not path:
        raise SystemExit(
            "set OPENCODE_PASSWORD or OPENCODE_PASSWORD_FILE "
            "(the latter should point at OpenCode's service.json)"
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
    except OSError as exc:
        raise SystemExit(f"cannot read OPENCODE_PASSWORD_FILE {path}: {exc}") from None
    if raw.startswith("{"):
        try:
            return json.loads(raw)["password"]
        except (ValueError, KeyError) as exc:
            raise SystemExit(f"{path} does not look like service.json: {exc}") from None
    return raw


def _created(message: dict) -> int:
    """Creation timestamp of a message, in epoch milliseconds."""
    return (message.get("time") or {}).get("created", 0)


# ------------------------------------------------------------ plain text output
#
# Delta Chat renders message text literally: its markdown parser was removed in
# the 2.x series, so `` `code` ``, **bold**, ##headings and | tables | all show
# up as the raw characters. The agent writes markdown regardless, so flatten it
# into something that reads well in a chat.

_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+")
_RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_BOLD_US_RE = re.compile(r"__(?=\S)(.+?)(?<=\S)__", re.S)
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
# Single-character emphasis is deliberately NOT stripped: `*word*` and a glob
# like `ls *foo*` are lexically identical, and mangling a command the user is
# being asked to approve is much worse than showing a stray asterisk. The
# underscore form is safe enough when it sits between spaces, which is also
# what keeps `foo_bar_baz`, `*.yaml *.conf` and `find / -name "*_db*"` intact.
_ITALIC_US_RE = re.compile(r"(?:^|(?<=\s))_(?=\S)([^_\n]+?)(?<=\S)_(?=\s|$)")


def _plain_line(line: str) -> str:
    line = _HEADING_RE.sub("", line)
    line = _QUOTE_RE.sub("", line)
    if _RULE_RE.match(line):
        return ""
    line = _LINK_RE.sub(
        lambda m: m.group(2)
        if m.group(1).strip() in ("", m.group(2))
        else f"{m.group(1)} ({m.group(2)})",
        line,
    )
    line = _BOLD_RE.sub(r"\1", line)
    line = _BOLD_US_RE.sub(r"\1", line)
    line = _STRIKE_RE.sub(r"\1", line)
    line = _ITALIC_US_RE.sub(r"\1", line)
    line = _BULLET_RE.sub(r"\1- ", line)
    return line


def _is_pipe_row(line: str) -> bool:
    stripped = line.strip()
    if stripped.count("|") < 2:
        return False
    # a leading-pipe markdown row, or the `a | b | c` style models often emit
    return stripped.startswith("|") or " | " in stripped


def _convert_tables(lines: list[str]) -> list[str]:
    """Render table rows as one line each; pipes mean nothing in Delta Chat.

    Only runs of three or more pipe-y lines are converted. A command such as
    `docker ps | head -4 | wc -l` also contains pipe separators, and rewriting
    one of those would corrupt the very text the user may be asked to approve.
    """
    out: list[str] = []
    index = 0
    while index < len(lines):
        if not _is_pipe_row(lines[index]):
            out.append(lines[index])
            index += 1
            continue

        end = index
        while end < len(lines) and _is_pipe_row(lines[end]):
            end += 1
        block = lines[index:end]
        index = end
        if len(block) < 3:
            out.extend(block)
            continue

        rows: list[Optional[list[str]]] = []
        for candidate in block:
            if _TABLE_SEP_RE.match(candidate) and "-" in candidate:
                rows.append(None)
            else:
                cells = candidate.strip().strip("|").split("|")
                rows.append([cell.strip() for cell in cells])

        # a header row followed by |---|---| carries no information in a chat
        if len(rows) >= 2 and rows[1] is None and rows[0] is not None:
            rows = rows[2:]
        for row in rows:
            if row and any(row):
                out.append("- " + " · ".join(cell for cell in row if cell))
    return out


def _plain_block(lines: list[str]) -> list[str]:
    spans: list[str] = []

    def stash(match: "re.Match[str]") -> str:
        spans.append(match.group(1))
        return f"\x00{len(spans) - 1}\x00"

    joined = _CODE_RE.sub(stash, "\n".join(lines))
    processed = _convert_tables([_plain_line(line) for line in joined.split("\n")])
    joined = "\n".join(processed)
    for i, span in enumerate(spans):
        joined = joined.replace(f"\x00{i}\x00", span)
    return joined.split("\n")


def plain_text(text: str) -> str:
    """Flatten markdown into plain text that reads well in Delta Chat."""
    blocks: list[tuple[str, list[str]]] = []
    kind, current, fenced = "prose", [], False
    for raw in (text or "").split("\n"):
        if _FENCE_RE.match(raw):
            blocks.append((kind, current))
            current = []
            fenced = not fenced
            kind = "code" if fenced else "prose"
            continue
        current.append(raw)
    blocks.append((kind, current))

    out: list[str] = []
    for block_kind, lines in blocks:
        # code blocks are already monospace-ish in intent; keep them verbatim
        out.extend(lines if block_kind == "code" else _plain_block(lines))

    # squeeze runs of blank lines down to one
    squeezed: list[str] = []
    blanks = 0
    for line in out:
        if line.strip():
            blanks = 0
            squeezed.append(line.rstrip())
        else:
            blanks += 1
            if blanks <= 1:
                squeezed.append("")
    return "\n".join(squeezed).strip()


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def chunk_text(text: str, limit: int = MAX_MSG_CHARS) -> list[str]:
    text = (text or "").strip() or "(no reply)"
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
        while len(current) > limit:
            chunks.append(current[:limit])
            current = current[limit:]
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------- bridge


class Bridge:
    def __init__(self, bot: Bot, oc: OpenCodeClient, cfg: Config, state: State) -> None:
        self.bot = bot
        self.oc = oc
        self.cfg = cfg
        self.state = state
        self._lock = threading.RLock()
        self._pending: dict[tuple[int, int], dict[str, Pending]] = {}
        self._queues: dict[tuple[int, int], queue.Queue] = {}
        self._threads: dict[tuple[int, int], threading.Thread] = {}
        self._accepted: set[tuple[int, int]] = set()
        self._declined: set[tuple[int, int]] = set()

    # ------------------------------------------------------------- messaging

    def send(self, accid: int, chat_id: int, text: str) -> None:
        for piece in chunk_text(text):
            try:
                self.bot.rpc.send_msg(accid, chat_id, MessageData(text=piece))
            except Exception:
                # A fresh contact request may need accepting before we can reply.
                try:
                    self.bot.rpc.accept_chat(accid, chat_id)
                    self.bot.rpc.send_msg(accid, chat_id, MessageData(text=piece))
                except Exception:
                    _log.exception("failed to send reply to chat %s", chat_id)  # type: ignore[union-attr]

    # ---------------------------------------------------------------- inbound

    def on_message(self, accid: int, event) -> None:
        msg = event.msg
        if msg.from_id <= SpecialContactId.LAST_SPECIAL:
            return
        chat_id = msg.chat_id
        sender = (msg.sender.address or "").lower()

        try:
            self.bot.rpc.markseen_msgs(accid, [msg.id])
        except Exception:
            pass

        # Fail closed: an empty ALLOWED_SENDERS refuses everybody, but replies
        # with the sender's own address so it can be pasted into .env.
        if sender not in self.cfg.allowed_senders:
            self.send(
                accid,
                chat_id,
                f"Sorry, this bot only serves its owner.\n\nYour address is:\n{sender}\n\n"
                "(Add it to ALLOWED_SENDERS in .env to allow it.)",
            )
            return

        key = (accid, chat_id)
        if key not in self._accepted:
            self._accepted.add(key)
            try:
                self.bot.rpc.accept_chat(accid, chat_id)
            except Exception:
                pass

        text = (msg.text or "").strip()
        if not text:
            self.send(accid, chat_id, "I can only read text messages.")
            return

        if self._handle_confirmation(accid, chat_id, text):
            return

        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if command in KNOWN_COMMANDS:
            self._handle_command(accid, chat_id, command, sender)
            return

        self._enqueue(accid, chat_id, text)

    def _handle_command(self, accid: int, chat_id: int, command: str, sender: str) -> None:
        if command in ("/help", "/start"):
            self.send(accid, chat_id, HELP)
        elif command == "/new":
            old = self.state.get_session(accid, chat_id)
            self.state.clear_session(accid, chat_id)
            with self._lock:
                self._pending.pop((accid, chat_id), None)
            if old:
                try:
                    self.oc.interrupt(old)
                except OpenCodeError:
                    pass
            self.send(accid, chat_id, "🧹 Fresh session. Nothing from before is remembered.")
        elif command == "/status":
            self._show_status(accid, chat_id)
        elif command == "/stop":
            session_id = self.state.get_session(accid, chat_id)
            if session_id:
                try:
                    self.oc.interrupt(session_id)
                except OpenCodeError:
                    pass
            self.send(accid, chat_id, "🛑 Asked the agent to stop.")
        elif command == "/whoami":
            self.send(accid, chat_id, f"You are messaging from:\n{sender}")

    def _show_status(self, accid: int, chat_id: int) -> None:
        session_id = self.state.get_session(accid, chat_id)
        if not session_id:
            self.send(accid, chat_id, "No session yet - just ask me something.")
            return
        try:
            info = self.oc.get_session(session_id)
        except OpenCodeError as exc:
            self.send(accid, chat_id, f"Session {session_id} is unreachable: {exc}")
            return
        tokens = info.get("tokens") or {}
        cost = info.get("cost") or 0
        lines = [
            f"session: {session_id}",
            f"agent:   {info.get('agent', '?')}",
            f"model:   {self._model_name(info)}",
            f"cost:    {f'${cost:.4f}' if cost else 'free'}",
            f"tokens:  {tokens.get('input', 0)} in / {tokens.get('output', 0)} out",
        ]
        if tokens.get("reasoning"):
            lines.append(f"thought: {tokens['reasoning']}")
        self.send(accid, chat_id, "\n".join(lines))

    def _model_name(self, info: dict) -> str:
        """Best available model name.

        A session created through the API records no model until one is chosen
        explicitly, so fall back to the newest assistant message, then to the
        model configured for the bridge.
        """
        def describe(ref: Optional[dict]) -> Optional[str]:
            if not ref or not ref.get("id"):
                return None
            provider = ref.get("providerID")
            return f"{ref['id']} ({provider})" if provider else str(ref["id"])

        name = describe(info.get("model")) or describe(self.cfg.model)
        if name:
            return name
        try:
            for message in self.oc.messages(info.get("id", "")):
                if message.get("type") == "assistant":
                    name = describe(message.get("model"))
                    if name:
                        return name
        except OpenCodeError:
            pass
        return "OpenCode default"

    def _handle_confirmation(self, accid: int, chat_id: int, text: str) -> bool:
        key = (accid, chat_id)
        with self._lock:
            pending = self._pending.get(key) or {}
            open_items = [p for p in pending.values() if not p.decision]
            if not open_items:
                return False

            if CONFIRM_RE.match(text):
                for item in open_items:
                    item.decision = "once"
                confirmation = "✅ Approved - running it now."
            elif REJECT_RE.match(text):
                for item in open_items:
                    item.decision = "reject"
                confirmation = "🚫 Rejected - dropping that action."
            else:
                lines = "\n".join(f"• {p.describe()}" for p in open_items)
                confirmation = (
                    "I am waiting for your decision before running:\n"
                    f"{lines}\n\n"
                    'Reply "yes" to allow it once, or "no" to reject it.'
                )

        self.send(accid, chat_id, confirmation)
        return True

    # --------------------------------------------------------------- sessions

    def _ensure_session(self, accid: int, chat_id: int, first_text: str) -> str:
        session_id = self.state.get_session(accid, chat_id)
        if session_id:
            try:
                if self.oc.session_exists(session_id):
                    return session_id
            except OpenCodeError:
                pass
            _log.warning("session %s is gone, creating a new one", session_id)  # type: ignore[union-attr]
            self.state.clear_session(accid, chat_id)

        title = f"{self.cfg.title_prefix} {first_text[:60]}".strip()
        data = self.oc.create_session(
            title=title,
            directory=self.cfg.session_dir,
            agent=self.cfg.agent,
            model=self.cfg.model,
        )
        session_id = data["id"]
        self.state.set_session(accid, chat_id, session_id)
        _log.info("chat %s -> new session %s", chat_id, session_id)  # type: ignore[union-attr]
        return session_id

    # ---------------------------------------------------------------- workers

    def _enqueue(self, accid: int, chat_id: int, text: str) -> None:
        key = (accid, chat_id)
        with self._lock:
            thread = self._threads.get(key)
            if thread is None or not thread.is_alive():
                work: queue.Queue = queue.Queue()
                self._queues[key] = work
                thread = threading.Thread(
                    target=self._worker,
                    args=(accid, chat_id, work),
                    name=f"chat-{accid}-{chat_id}",
                    daemon=True,
                )
                self._threads[key] = thread
                thread.start()
            self._queues[key].put(text)

    def _worker(self, accid: int, chat_id: int, work: queue.Queue) -> None:
        while True:
            item = work.get()
            try:
                if item is None:
                    return
                self._run(accid, chat_id, item)
            except Exception:
                _log.exception("run failed for chat %s", chat_id)  # type: ignore[union-attr]
                self.send(accid, chat_id, "⚠️ Something went wrong handling that message.")
            finally:
                work.task_done()

    def _run(self, accid: int, chat_id: int, text: str) -> None:
        key = (accid, chat_id)
        session_id = self._ensure_session(accid, chat_id, text)
        with self._lock:
            self._pending[key] = {}
            self._declined.discard(key)

        started = time.time()
        try:
            self.oc.prompt(session_id, text)
        except OpenCodeError as exc:
            self.send(accid, chat_id, f"⚠️ OpenCode refused the request: {exc}")
            return

        seen: set[str] = set()
        acked = False
        waiter: Optional[threading.Thread] = None
        idle_returns = 0

        while True:
            self._sync_permissions(accid, chat_id, session_id, seen)

            if waiter is not None and not waiter.is_alive():
                if self._evidence(session_id, started):
                    self._finish(accid, chat_id, session_id, started)
                    return
                # /wait can come back before the run has really concluded, for
                # instance in the gap between enqueueing the prompt and the
                # execution starting, or after an external interrupt. Re-arm a
                # couple of times before concluding the run is over.
                idle_returns += 1
                if idle_returns >= 3:
                    self._finish(accid, chat_id, session_id, started)
                    return
                waiter = None

            if waiter is None:
                waiter = threading.Thread(
                    target=self._wait_for_idle,
                    args=(session_id,),
                    name=f"wait-{session_id}",
                    daemon=True,
                )
                waiter.start()

            elapsed = time.time() - started
            if elapsed > self.cfg.timeout:
                self._clear_pending(key)
                try:
                    self.oc.interrupt(session_id)
                except OpenCodeError:
                    pass
                self.send(
                    accid,
                    chat_id,
                    f"⏱️ Gave up after {int(self.cfg.timeout)}s and stopped the run.",
                )
                return

            if not acked and elapsed > self.cfg.ack_after:
                acked = True
                self.send(accid, chat_id, "⏳ Still working on it…")

            time.sleep(1.0)

    def _sync_permissions(
        self, accid: int, chat_id: int, session_id: str, seen: set[str]
    ) -> int:
        """Register new approval requests, deliver decisions, and report how many
        requests are still unanswered."""
        key = (accid, chat_id)
        try:
            requests = self.oc.pending_permissions(session_id)
        except OpenCodeError:
            requests = []

        live = {r.get("id") for r in requests if r.get("id")}
        fresh = []
        with self._lock:
            pending = self._pending.setdefault(key, {})
            for request in requests:
                request_id = request.get("id")
                if not request_id or request_id in seen:
                    continue
                seen.add(request_id)
                pending[request_id] = Pending(
                    request_id=request_id,
                    action=request.get("action", "?"),
                    resources=request.get("resources") or [],
                    asked_at=time.time(),
                    reason=self._rationale(session_id, request),
                )
                fresh.append(pending[request_id])

            for request_id, item in list(pending.items()):
                if item.decision and not item.replied:
                    try:
                        self.oc.reply_permission(session_id, request_id, item.decision)
                    except OpenCodeError as exc:
                        _log.warning("could not reply to %s: %s", request_id, exc)  # type: ignore[union-attr]
                    item.replied = True
                    if item.decision == "reject":
                        self._declined.add(key)
                if item.replied and request_id not in live:
                    pending.pop(request_id, None)

            remaining = sum(1 for p in pending.values() if not p.replied)

        for item in fresh:
            why = f"\n\nWhy: {item.reason}" if item.reason else ""
            self.send(
                accid,
                chat_id,
                "🔐 The agent wants to run:\n\n"
                f"{item.describe()}{why}\n\n"
                'Reply "yes" to allow it once, or "no" to reject it.',
            )
        return remaining

    def _rationale(self, session_id: str, request: dict) -> str:
        """The sentence the agent wrote just before asking, if it wrote one.

        The permission request itself carries no explanation, but it points at
        the assistant message containing the tool call, and any prose the model
        put in front of that call is worth showing next to the command.
        """
        message_id = (request.get("source") or {}).get("messageID")
        if not message_id:
            return ""
        try:
            for message in self.oc.messages(session_id):
                if message.get("id") != message_id:
                    continue
                prose = " ".join(
                    (item.get("text") or "").strip()
                    for item in (message.get("content") or [])
                    if item.get("type") == "text"
                )
                return _clip(prose, 240)
        except OpenCodeError:
            pass
        return ""

    def _clear_pending(self, key: tuple[int, int]) -> None:
        with self._lock:
            self._pending.pop(key, None)

    def _wait_for_idle(self, session_id: str) -> None:
        """Block until OpenCode reports the session idle (runs in a thread)."""
        try:
            self.oc.wait(session_id)
        except OpenCodeError as exc:
            _log.debug("wait on %s ended with %s", session_id, exc)  # type: ignore[union-attr]

    def _evidence(self, session_id: str, started: float) -> bool:
        """True once this run has produced a terminal assistant message.

        ``/wait`` reporting the session idle is necessary but not sufficient:
        the session can be idle before the run starts, or after something
        interrupted it. Wait for the run's own ending before reporting.
        """
        try:
            items = self.oc.messages(session_id)
        except OpenCodeError:
            return False

        threshold = started * 1000
        for item in items:
            if item.get("type") == "idle" and _created(item) >= threshold:
                return True

        newest: Optional[dict] = None
        for item in items:
            if item.get("type") != "assistant" or _created(item) < threshold:
                continue
            if newest is None or _created(item) > _created(newest):
                newest = item
        if newest is None:
            return False
        finish = newest.get("finish")
        # "tool-calls" means another step follows; any other non-null value is
        # the end of the run ("stop", "error", ...).
        return bool(finish) and finish != "tool-calls"

    def _finish(
        self, accid: int, chat_id: int, session_id: str, started: float
    ) -> None:
        key = (accid, chat_id)
        self._clear_pending(key)
        with self._lock:
            declined = key in self._declined
            self._declined.discard(key)

        try:
            items = self.oc.messages(session_id)
        except OpenCodeError:
            items = []

        threshold = started * 1000
        status = "succeeded"
        for item in items:
            if item.get("type") == "idle" and _created(item) >= threshold:
                status = item.get("outcome") or "succeeded"
                break
        replies = sorted(
            (
                m
                for m in items
                if m.get("type") == "assistant" and _created(m) >= threshold
            ),
            key=_created,
        )

        texts: list[str] = []
        narrated: list[str] = []
        tools: list[str] = []
        for message in replies:
            content = message.get("content") or []
            # A message that carries a tool call is the model narrating what it
            # is about to do ("Quick disk check since root is small:"), however
            # that step ends - a declined command finishes with "error". Keeping
            # the narration makes the reply read as a duplicate of the answer,
            # so it is only used if the run produced no answer text at all.
            narrating = message.get("finish") == "tool-calls" or any(
                self._is_tool(item) for item in content
            )
            for item in content:
                if item.get("type") == "reasoning":
                    continue
                if self._is_tool(item):
                    state = item.get("state") or {}
                    if state.get("status") != "completed":
                        continue
                    # A command that ran and exited non-zero (a typo the model
                    # then corrected, say) is noise in the footer, not evidence.
                    exit_code = (state.get("metadata") or {}).get("exit")
                    if isinstance(exit_code, int) and exit_code != 0:
                        continue
                    label = self._tool_label(item)
                    if label:
                        tools.append(label)
                elif item.get("type") == "text" and (item.get("text") or "").strip():
                    (narrated if narrating else texts).append(item["text"].strip())

        body = plain_text("\n\n".join(texts))
        if not body:
            if declined:
                body = "🚫 You declined that, so nothing was run."
            elif status == "interrupted":
                body = "🛑 That run was interrupted."
            elif status == "failed":
                body = "⚠️ That run ended without an answer - it hit an error."
            else:
                # no final answer text: the model only narrated around its tool
                # calls, which is still better than reporting nothing
                body = plain_text("\n\n".join(narrated)) or "I did not get an answer for that."

        if self.cfg.show_tools and tools:
            commands = [c for c in dict.fromkeys(_clip(t, 200) for t in tools) if c]
            shown = commands[:3]
            if len(shown) == 1:
                body += f"\n\nran: {shown[0]}"
            elif shown:
                body += "\n\nran:\n" + "\n".join(f"- {c}" for c in shown)
                if len(commands) > len(shown):
                    body += f"\n(+{len(commands) - len(shown)} more)"

        self.send(accid, chat_id, body)

    @staticmethod
    def _is_tool(item: dict) -> bool:
        return bool(item.get("name")) and isinstance(item.get("state"), dict)

    @staticmethod
    def _tool_label(item: dict) -> str:
        name = item.get("name") or "tool"
        state = item.get("state") or {}
        args = state.get("input") or {}
        if name == "shell" and args.get("command"):
            return str(args["command"])
        detail = args.get("path") or args.get("filePath") or args.get("pattern") or ""
        return f"{name} {detail}".strip()


# ----------------------------------------------------------------------- hooks


@cli.on(events.NewMessage)
def _on_new_message(bot: Bot, accid: int, event) -> None:
    if _bridge is not None:
        _bridge.on_message(accid, event)


@cli.on_start
def _on_start(bot: Bot, args) -> None:
    """Runs for `serve` only, after the account is confirmed configured."""
    global _bridge, _log
    _log = bot.logger

    cfg = _load_config()
    oc = OpenCodeClient(
        os.environ.get("OPENCODE_URL", "http://127.0.0.1:49374"),
        _load_password(),
    )
    try:
        info = oc.info()
    except OpenCodeError as exc:
        raise SystemExit(
            f"cannot reach the OpenCode server at {oc.url}: {exc}\n"
            "Is `opencode serve` running, and is the container on host networking?"
        ) from None
    _log.info("connected to OpenCode %s at %s", info.get("version"), oc.url)

    if not os.path.isdir(cfg.session_dir):
        _log.warning(
            "OPENCODE_SESSION_DIR %s does not exist; sessions may fail to start",
            cfg.session_dir,
        )
    if not cfg.allowed_senders:
        _log.warning(
            "ALLOWED_SENDERS is empty: every sender is refused (the bot will tell "
            "them their own address so it can be added to .env)"
        )
    else:
        _log.info("allowed senders: %s", ", ".join(sorted(cfg.allowed_senders)))

    state = State(os.environ.get("STATE_DB", "/data/state.sqlite"))
    _bridge = Bridge(bot, oc, cfg, state)

    accids = [args.account] if getattr(args, "account", None) else bot.rpc.get_all_account_ids()
    for accid in accids:
        if not bot.rpc.is_configured(accid):
            continue
        try:
            bot.rpc.set_config(accid, "bot", "1")
            bot.rpc.set_config(accid, "displayname", "Home Server")
            me = bot.rpc.get_contact(accid, SpecialContactId.SELF)
            _log.info("bot address: %s (account %s)", me.address, accid)
        except Exception as exc:
            _log.warning("account %s setup skipped: %s", accid, exc)
