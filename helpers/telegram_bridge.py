"""Persistent Telegram bot for the chat bridge.
Uses python-telegram-bot's Application with polling to receive messages
and routes them through Agent Zero's LLM.

SECURITY MODEL:
  - Restricted mode (default): Uses call_utility_model() — NO tools, NO code execution,
    NO file access. The LLM literally cannot perform system operations.
  - Elevated mode (opt-in): Authenticated users get full agent loop access via
    context.communicate(). Requires: allow_elevated=true in config + runtime auth
    via !auth <key> in Telegram. Sessions expire after a configurable timeout.
"""

import asyncio
import collections
import hmac
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("telegram_chat_bridge")

# Protects all load-mutate-save cycles on the chat state file so concurrent
# tool calls (running on the bridge thread or a separate thread) cannot
# interleave and silently overwrite each other's updates.
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _topic_key(chat_id: str, thread_id) -> str:
    """Build context/store key. Returns '{chat_id}:topic:{thread_id}' for topics."""
    if thread_id:
        return f"{chat_id}:topic:{thread_id}"
    return chat_id


def _conversation_key(
    chat_id: str,
    thread_id,
    user_id: Optional[str],
    per_user: bool,
) -> str:
    """Build context key with optional per-user isolation in group chats."""
    base = _topic_key(chat_id, thread_id)
    if per_user and user_id and thread_id is None:
        return f"{base}:user:{user_id}"
    return base


async def _safe_react(bot, chat_id: str, message_id: int, emoji: str, config: dict):
    """Fire-and-forget reaction setter. Never raises."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[{"type": "emoji", "emoji": emoji}],
        )
    except Exception as e:
        err = str(e).lower()
        if "not modified" in err or "bad request" in err or "reaction" in err:
            logger.debug(f"Reaction {emoji} not supported in chat {chat_id}: {e}")
        else:
            logger.debug(f"Reaction failed ({type(e).__name__}): {e}")


# Singleton bot instance and its dedicated event loop thread
_bot_instance: Optional["ChatBridgeBot"] = None
_bot_thread: Optional[threading.Thread] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_auto_start_attempted: bool = False

# Prevents concurrent start_chat_bridge calls from racing past the
# _bot_instance guard and spawning duplicate getUpdates sessions.
_start_lock = threading.Lock()

# Set when the bridge encounters a fatal, non-recoverable error (e.g. invalid
# bot token).  The watchdog checks this flag and stops retrying instead of
# entering an infinite restart loop.  Cleared by start_chat_bridge() so a
# user can recover by updating their token and clicking "Start Bridge".
_fatal_error: Optional[str] = None       # human-readable reason
_fatal_error_type: Optional[str] = None  # "token" | "config" | "unknown"

CHAT_STATE_FILE = "chat_bridge_state.json"


def _get_state_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / CHAT_STATE_FILE,
        Path("/a0/usr/plugins/telegram/data") / CHAT_STATE_FILE,
        Path("/a0/plugins/telegram/data") / CHAT_STATE_FILE,
        Path("/git/agent-zero/usr/plugins/telegram/data") / CHAT_STATE_FILE,
    ]
    for p in candidates:
        if p.exists():
            return p
    path = candidates[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_chat_state() -> dict:
    path = _get_state_path()
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {"chats": {}, "contexts": {}}


def save_chat_state(state: dict):
    from usr.plugins.telegram.helpers.sanitize import secure_write_json
    secure_write_json(_get_state_path(), state)


def add_chat(chat_id: str, label: str = ""):
    with _state_lock:
        state = load_chat_state()
        state.setdefault("chats", {})[chat_id] = {
            "label": label or chat_id,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        save_chat_state(state)


def remove_chat(chat_id: str):
    with _state_lock:
        state = load_chat_state()
        state.get("chats", {}).pop(chat_id, None)
        state.get("contexts", {}).pop(chat_id, None)
        save_chat_state(state)


def get_chat_list() -> dict:
    return load_chat_state().get("chats", {})


def get_context_id(chat_id: str) -> Optional[str]:
    return load_chat_state().get("contexts", {}).get(chat_id)


def set_context_id(chat_id: str, context_id: str):
    with _state_lock:
        state = load_chat_state()
        state.setdefault("contexts", {})[chat_id] = context_id
        save_chat_state(state)


def get_topic_map() -> dict:
    """Return the full topic->project mapping dict."""
    return load_chat_state().get("topics", {})


def set_topic_project(
    topic_key: str,
    project_id: str,
    name: str,
    auto_created: bool = False,
):
    """Map a topic key to a project and name."""
    import time as _time
    with _state_lock:
        state = load_chat_state()
        existing = state.get("topics", {}).get(topic_key, {})
        state.setdefault("topics", {})[topic_key] = {
            "name": name,
            "project_id": project_id,
            "created_at": existing.get("created_at", _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())),
            "last_active_at": _time.time(),
            "auto_created": auto_created,
        }
        save_chat_state(state)


def get_topic_project(topic_key: str) -> Optional[dict]:
    """Get project info for a topic key, or None."""
    return load_chat_state().get("topics", {}).get(topic_key)


def touch_topic(topic_key: str):
    """Update last_active_at for a topic (for idle-timeout tracking)."""
    with _state_lock:
        state = load_chat_state()
        topic = state.get("topics", {}).get(topic_key)
        if topic:
            topic["last_active_at"] = time.time()
            save_chat_state(state)


# Bot commands list (registered with Telegram on startup)
BRIDGE_COMMANDS = [
    {"command": "auth",       "description": "Elevate to full Agent Zero access. Usage: /auth key"},
    {"command": "deauth",     "description": "End elevated session"},
    {"command": "status",     "description": "Show current session mode"},
    {"command": "help",       "description": "List available commands"},
    {"command": "newcontext", "description": "Clear conversation history and start fresh"},
    {"command": "cancel",     "description": "Cancel the current in-progress task"},
]


class ChatBridgeBot:
    """Telegram bot that bridges messages to Agent Zero's LLM.

    SECURITY: By default, uses direct LLM calls (call_utility_model) with NO
    tool access. Authenticated users can optionally elevate to full agent loop
    access if allow_elevated is enabled in the plugin config.
    """

    MAX_CHAT_MESSAGE_LENGTH = 4096
    MAX_HISTORY_MESSAGES = 20
    # Rate limit: max messages per user within the window
    RATE_LIMIT_MAX = 10
    RATE_LIMIT_WINDOW = 60  # seconds
    # Auth failure rate limit
    AUTH_MAX_FAILURES = 5
    AUTH_FAILURE_WINDOW = 300  # 5 minute lockout

    CHAT_SYSTEM_PROMPT = (
        "You are a friendly, helpful AI assistant chatting with users on Telegram.\n\n"
        "IMPORTANT CONSTRAINTS:\n"
        "- You are a conversational chat bot ONLY. You have NO access to tools, files, "
        "commands, terminals, or any system resources.\n"
        "- If users ask you to run commands, access files, list directories, execute code, "
        "or perform any system operations, explain that you don't have those capabilities.\n"
        "- NEVER fabricate or make up file listings, directory contents, command outputs, "
        "or system information. You genuinely do not have access to any of these.\n"
        "- Be helpful, friendly, and conversational within these constraints.\n"
        "- You can help with general knowledge, answer questions, have discussions, "
        "write text, brainstorm ideas, and more — just not anything involving system access.\n"
        "- Each message shows the Telegram username prefix. Respond naturally to the "
        "conversation.\n"
    )

    def __init__(self, bot_token: str):
        if not bot_token or not bot_token.strip():
            raise ValueError("Bot token must be provided to ChatBridgeBot.")
        self.bot_token = bot_token
        self._running = False
        self._application = None
        self._bot_user = None
        # Per-user rate limiting: user_id -> deque of timestamps
        self._rate_limits: dict[str, collections.deque] = {}
        # Per-chat conversation history (in-memory, lost on restart)
        self._conversations: dict[str, list[dict]] = {}
        # Elevated session tracking: "{user_id}:{chat_id}" -> {"at": float, "name": str}
        self._elevated_sessions: dict[str, dict] = {}
        # Failed auth attempt tracking: user_id -> deque of timestamps
        self._auth_failures: dict[str, collections.deque] = {}
        # Temp files for image attachments in elevated mode
        self._temp_files: list[str] = []
        # Threading event for signaling ready state
        self._ready_event: Optional[threading.Event] = None
        # Pending inline keyboard approvals:
        # "{chat_id}:{message_id}" -> {"future": Future, "message_text": str, "requester_user_id": str}
        self._pending_approvals: dict[str, dict] = {}
        # Tracks sent replies for edited-message handling: "{chat_id}:{user_msg_id}" -> bot_reply_msg_id
        self._sent_replies: dict[str, int] = {}
        # Concurrency semaphore (initialized lazily in _get_semaphore to use running loop)
        self._concurrency_sem: Optional[asyncio.Semaphore] = None
        # Cancel flags: chat_key -> True if cancel requested
        self._cancel_requested: dict[str, bool] = {}
        # Last time a message was fully processed (unix timestamp); used by watchdog
        self._last_activity_ts: float = 0.0

    # ------------------------------------------------------------------
    # Config access
    # ------------------------------------------------------------------

    def _get_config(self) -> dict:
        """Load the Telegram plugin configuration."""
        try:
            from usr.plugins.telegram.helpers.telegram_client import get_telegram_config
            return get_telegram_config()
        except Exception:
            return {}

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily initialize the concurrency semaphore."""
        if self._concurrency_sem is None:
            config = self._get_config()
            max_concurrent = config.get("chat_bridge", {}).get("max_concurrent", 3)
            self._concurrency_sem = asyncio.Semaphore(max_concurrent)
        return self._concurrency_sem

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _session_key(self, user_id: str, chat_id: str) -> str:
        return f"{user_id}:{chat_id}"

    def _is_elevated(self, user_id: str, chat_id: str) -> bool:
        """Check if a user has an active elevated session in this chat."""
        config = self._get_config()
        if not config.get("chat_bridge", {}).get("allow_elevated", False):
            return False

        key = self._session_key(user_id, chat_id)
        session = self._elevated_sessions.get(key)
        if not session:
            return False

        timeout = config.get("chat_bridge", {}).get("session_timeout", 300)
        # timeout=0 means never expire
        if timeout > 0 and time.monotonic() - session["at"] > timeout:
            del self._elevated_sessions[key]
            return False

        return True

    def _get_auth_key(self, config: dict) -> str:
        """Get the auth key from config, auto-generating if needed."""
        bridge_config = config.get("chat_bridge", {})
        auth_key = bridge_config.get("auth_key", "")

        if not auth_key and bridge_config.get("allow_elevated", False):
            from usr.plugins.telegram.helpers.sanitize import generate_auth_key
            auth_key = generate_auth_key()
            bridge_config["auth_key"] = auth_key
            config["chat_bridge"] = bridge_config
            try:
                from usr.plugins.telegram.helpers.sanitize import secure_write_json
                config_candidates = [
                    Path("/a0/usr/plugins/telegram/config.json"),
                    Path("/a0/plugins/telegram/config.json"),
                    Path(__file__).parent.parent / "config.json",
                ]
                for cp in config_candidates:
                    if cp.exists():
                        existing = json.loads(cp.read_text())
                        existing.setdefault("chat_bridge", {})["auth_key"] = auth_key
                        secure_write_json(cp, existing)
                        logger.info("Auto-generated auth key for elevated mode")
                        break
            except Exception as e:
                logger.warning(f"Could not persist auto-generated auth key: {type(e).__name__}")

        return auth_key

    # ------------------------------------------------------------------
    # Auth command handling
    # ------------------------------------------------------------------

    async def _handle_auth_command(self, update, context_obj) -> bool:
        """Handle !auth, !deauth, and !bridge-status commands.

        Returns True if the message was an auth command (consumed), False otherwise.
        """
        message = update.message
        text = message.text.strip()
        user_id = str(message.from_user.id)
        chat_id = str(message.chat_id)

        # --- !deauth ---
        if text.lower() in ("!deauth", "!dauth", "!unauth", "!logout", "!logoff"):
            key = self._session_key(user_id, chat_id)
            if key in self._elevated_sessions:
                del self._elevated_sessions[key]
                self._conversations.pop(chat_id, None)
                await message.reply_text("Session ended. Back to restricted mode.")
                logger.info(f"Elevated session ended: user={user_id} chat={chat_id}")
            else:
                await message.reply_text("No active elevated session.")
            return True

        # --- !bridge-status / !status ---
        if text.lower() in ("!bridge-status", "!status"):
            if self._is_elevated(user_id, chat_id):
                session = self._elevated_sessions[self._session_key(user_id, chat_id)]
                elapsed = int(time.monotonic() - session["at"])
                config = self._get_config()
                timeout = config.get("chat_bridge", {}).get("session_timeout", 300)
                if timeout > 0:
                    remaining = max(0, timeout - elapsed)
                    expire_info = f"Session expires in {remaining // 60}m {remaining % 60}s"
                else:
                    expire_info = "Session does not expire"
                await message.reply_text(
                    f"Mode: *Elevated* (full agent access)\n"
                    f"{expire_info}. Use `!deauth` to end.",
                    parse_mode="Markdown",
                )
            else:
                config = self._get_config()
                elevated_available = config.get("chat_bridge", {}).get("allow_elevated", False)
                if elevated_available:
                    await message.reply_text(
                        "Mode: *Restricted* (chat only). Use `!auth <key>` to elevate.",
                        parse_mode="Markdown",
                    )
                else:
                    await message.reply_text(
                        "Mode: *Restricted* (chat only). Elevated mode is not enabled.",
                        parse_mode="Markdown",
                    )
            return True

        # --- !auth <key> ---
        if text.lower().startswith("!auth"):
            # Try to delete the message immediately to protect the key
            try:
                await message.delete()
            except Exception:
                logger.warning("Could not delete !auth message — bot lacks permission")

            config = self._get_config()
            if not config.get("chat_bridge", {}).get("allow_elevated", False):
                await context_obj.bot.send_message(
                    chat_id=chat_id,
                    text="Elevated mode is not enabled in the configuration.",
                )
                return True

            auth_key = self._get_auth_key(config)
            if not auth_key:
                await context_obj.bot.send_message(
                    chat_id=chat_id,
                    text="Elevated mode is enabled but no auth key could be generated. "
                         "Check plugin configuration.",
                )
                return True

            # Check auth failure rate limit (keyed per user+chat to isolate lockouts)
            now = time.monotonic()
            failure_key = f"{user_id}:{chat_id}"
            if failure_key not in self._auth_failures:
                self._auth_failures[failure_key] = collections.deque()
            failures = self._auth_failures[failure_key]
            while failures and now - failures[0] > self.AUTH_FAILURE_WINDOW:
                failures.popleft()
            if len(failures) >= self.AUTH_MAX_FAILURES:
                await context_obj.bot.send_message(
                    chat_id=chat_id,
                    text="Too many failed attempts. Please wait before trying again.",
                )
                return True

            # Extract the key from the command
            parts = text.split(maxsplit=1)
            provided_key = parts[1].strip() if len(parts) > 1 else ""

            # Constant-time comparison to prevent timing attacks
            if provided_key and hmac.compare_digest(provided_key, auth_key):
                session_key = self._session_key(user_id, chat_id)
                self._elevated_sessions[session_key] = {
                    "at": now,
                    "name": message.from_user.first_name or message.from_user.username or "user",
                }
                timeout = config.get("chat_bridge", {}).get("session_timeout", 300)
                if timeout > 0:
                    mins = timeout // 60
                    secs = timeout % 60
                    duration = f"{mins}m" if not secs else f"{mins}m {secs}s"
                    expire_msg = f"Session expires in {duration}."
                else:
                    expire_msg = "Session does not expire."
                await context_obj.bot.send_message(
                    chat_id=chat_id,
                    text=f"Elevated session active. {expire_msg} "
                         f"You now have full Agent Zero access in this chat. "
                         f"Use `!deauth` to end the session.",
                )
                logger.info(f"Elevated session granted: user={user_id} chat={chat_id}")
            else:
                failures.append(now)
                remaining = self.AUTH_MAX_FAILURES - len(failures)
                await context_obj.bot.send_message(
                    chat_id=chat_id,
                    text=f"Authentication failed. {remaining} attempt(s) remaining.",
                )
                logger.warning(f"Failed auth attempt: user={user_id} chat={chat_id} key={failure_key}")

            return True

        # Unknown ! command — don't pass to LLM
        await context_obj.bot.send_message(
            chat_id=chat_id,
            text="Unknown command. Available: `!auth <key>`, `!deauth`, `!status`",
            parse_mode="Markdown",
        )
        return True

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _on_message(self, update, context_obj):
        """Handle incoming Telegram messages."""
        message = update.message
        if not message:
            return
        # Handle text OR voice/audio
        if not message.text and not message.voice and not message.audio and not message.document and not message.photo:
            return

        # Ignore own messages
        if message.from_user and message.from_user.is_bot:
            return

        chat_id = str(message.chat_id)
        thread_id = getattr(message, "message_thread_id", None)
        user_id = str(message.from_user.id) if message.from_user else "unknown"

        # Store message for telegram_read tool access
        msg_text = (message.text or "").strip()
        if not msg_text.startswith("!") and not msg_text.startswith("/"):
            try:
                from usr.plugins.telegram.helpers.message_store import store_message
                raw_msg = {
                    "message_id": message.message_id,
                    "date": int(message.date.timestamp()) if message.date else 0,
                    "chat": {"id": message.chat_id, "type": message.chat.type,
                             "title": getattr(message.chat, "title", ""),
                             "first_name": getattr(message.chat, "first_name", ""),
                             "username": getattr(message.chat, "username", "")},
                    "text": message.text or "",
                }
                if thread_id:
                    raw_msg["message_thread_id"] = thread_id
                if message.from_user:
                    raw_msg["from"] = {
                        "id": message.from_user.id,
                        "first_name": message.from_user.first_name or "",
                        "last_name": message.from_user.last_name or "",
                        "username": message.from_user.username or "",
                        "is_bot": message.from_user.is_bot,
                    }
                if message.reply_to_message:
                    ref = message.reply_to_message
                    raw_msg["reply_to_message"] = {
                        "message_id": ref.message_id,
                        "from": {"first_name": getattr(ref.from_user, "first_name", "Unknown")} if ref.from_user else {},
                    }
                store_message(chat_id, raw_msg)
            except Exception as e:
                logger.debug(f"Could not store message: {e}")

        chat_list = get_chat_list()
        conv_key = _topic_key(chat_id, thread_id)

        # Only respond in designated chats OR their topics
        if chat_list:
            # Check both the topic key and the base chat_id
            if conv_key not in chat_list and chat_id not in chat_list:
                return

        # User allowlist
        config = self._get_config()
        allowed_users = config.get("chat_bridge", {}).get("allowed_users", [])
        if allowed_users and user_id not in [str(u) for u in allowed_users]:
            return

        # Determine user text (handle voice transcription)
        if message.voice or message.audio:
            voice_config = config.get("chat_bridge", {}).get("voice", {})
            if not voice_config.get("enabled", True):
                return
            asyncio.create_task(_safe_react(
                context_obj.bot, chat_id, message.message_id,
                config.get("reactions", {}).get("pre_react", "👍"), config
            ))
            user_text = await self._transcribe_voice(message)
            if not user_text:
                await message.reply_text("⚠️ Could not transcribe voice message. Please send text.")
                return
            # Sanitize transcript before any further processing (prompt injection
            # defence mirrors the sanitization applied to typed text).
            from usr.plugins.telegram.helpers.sanitize import sanitize_content as _sc
            user_text = _sc(user_text)
        else:
            user_text = message.text

        if not user_text or not user_text.strip():
            return

        # Handle auth/command prefix first
        if user_text.strip().startswith("!"):
            handled = await self._handle_auth_command(update, context_obj)
            if handled:
                return

        # Enforce content length
        if len(user_text) > self.MAX_CHAT_MESSAGE_LENGTH:
            await message.reply_text(f"Message too long ({len(user_text)} chars). Max: {self.MAX_CHAT_MESSAGE_LENGTH}.")
            return

        # Per-user rate limiting (existing code)
        user_key = user_id
        now = time.monotonic()
        if user_key not in self._rate_limits:
            self._rate_limits[user_key] = collections.deque()
        timestamps = self._rate_limits[user_key]
        while timestamps and now - timestamps[0] > self.RATE_LIMIT_WINDOW:
            timestamps.popleft()
        if len(timestamps) >= self.RATE_LIMIT_MAX:
            await message.reply_text(f"Rate limit: max {self.RATE_LIMIT_MAX} messages per {self.RATE_LIMIT_WINDOW}s.")
            return
        timestamps.append(now)

        # TRACK 1: React 👍 (received) — fire and forget BEFORE semaphore
        # Config path: top-level "reactions" key (not nested under chat_bridge)
        reactions_config = config.get("reactions", {})
        if reactions_config.get("enabled", True):
            asyncio.create_task(_safe_react(
                context_obj.bot, chat_id, message.message_id,
                reactions_config.get("pre_react", "👍"), config
            ))

        # Show typing while waiting for semaphore
        await context_obj.bot.send_chat_action(chat_id=chat_id, action="typing")

        is_elevated = self._is_elevated(user_id, chat_id)

        # TRACK 12: Concurrency semaphore
        async with self._get_semaphore():
            # TRACK 1: React 🤔 (thinking)
            if reactions_config.get("enabled", True):
                asyncio.create_task(_safe_react(
                    context_obj.bot, chat_id, message.message_id,
                    reactions_config.get("processing_react", "🤔"), config
                ))

            try:
                config2 = self._get_config()
                # Streaming config lives at top-level "streaming" key
                streaming_cfg = config2.get("streaming", {})
                streaming_enabled = streaming_cfg.get("enabled", True)
                per_user = config2.get("chat_bridge", {}).get("per_user_context", False)
                conv_key2 = _conversation_key(
                    chat_id, thread_id, user_id,
                    per_user and message.chat.type in ("group", "supergroup"),
                )

                # Touch topic last-active timestamp
                if thread_id:
                    touch_topic(_topic_key(chat_id, thread_id))

                if is_elevated:
                    response_text = await self._get_elevated_response(
                        conv_key2, user_text, message, thread_id=thread_id
                    )
                else:
                    response_text = await self._get_agent_response(
                        conv_key2, user_text, message
                    )

                # TRACK 2: Streaming or normal send
                if streaming_enabled and response_text:
                    from usr.plugins.telegram.helpers.stream_response import stream_text_to_telegram
                    from usr.plugins.telegram.helpers.message_store import store_message as _store_msg
                    await stream_text_to_telegram(
                        bot=context_obj.bot,
                        chat_id=chat_id,
                        reply_to_message_id=message.message_id,
                        full_text=response_text,
                        mode=streaming_cfg.get("mode", "sentence"),
                        edit_interval_ms=streaming_cfg.get("edit_interval_ms", 1500),
                        placeholder=streaming_cfg.get("placeholder", "…"),
                        message_thread_id=thread_id,
                        store_callback=_store_msg,
                    )
                else:
                    sent_id = await self._send_response(message, response_text, thread_id=thread_id)
                    # Track sent reply for edited-message handling
                    if sent_id:
                        key = f"{chat_id}:{message.message_id}"
                        self._sent_replies[key] = sent_id
                        # Cap dict size
                        if len(self._sent_replies) > 500:
                            oldest = next(iter(self._sent_replies))
                            del self._sent_replies[oldest]

                # Update watchdog activity timestamp on successful response
                self._last_activity_ts = time.time()

                # TRACK 1: React ✅ (done)
                if reactions_config.get("enabled", True):
                    asyncio.create_task(_safe_react(
                        context_obj.bot, chat_id, message.message_id,
                        reactions_config.get("success_react", "✅"), config2
                    ))

            except Exception as e:
                logger.error("Agent error: %s", type(e).__name__, exc_info=True)
                # TRACK 1: React ❌ (error)
                if reactions_config.get("enabled", True):
                    asyncio.create_task(_safe_react(
                        context_obj.bot, chat_id, message.message_id,
                        reactions_config.get("failure_react", "❌"), config
                    ))
                await self._send_response(message, "An error occurred while processing your message.", thread_id=thread_id)

    # ------------------------------------------------------------------
    # Restricted mode: direct LLM call, NO tools
    # ------------------------------------------------------------------

    async def _get_agent_response(self, conv_key: str, text: str, message) -> str:
        """Get LLM response via direct model call (no agent loop, no tools)."""
        try:
            from agent import AgentContext, AgentContextType
            from initialize import initialize_agent

            context_id = get_context_id(conv_key)
            context = None

            if context_id:
                context = AgentContext.get(context_id)

            if context is None:
                config = initialize_agent()
                context = AgentContext(config=config, type=AgentContextType.USER)
                set_context_id(conv_key, context.id)
                logger.info(f"Created new context {context.id} for key {conv_key}")

            agent = context.agent0

            from usr.plugins.telegram.helpers.sanitize import sanitize_content, sanitize_username
            from usr.plugins.telegram.helpers.conversation_store import load_history, save_history

            author_name = sanitize_username(
                message.from_user.first_name or message.from_user.username or "User"
            ) if message.from_user else "User"
            safe_text = sanitize_content(text)

            # Load from persistent store (write-through cache)
            if conv_key not in self._conversations:
                self._conversations[conv_key] = load_history(conv_key)
            history = self._conversations[conv_key]
            history.append({"role": "user", "name": author_name, "content": safe_text})

            if len(history) > self.MAX_HISTORY_MESSAGES:
                self._conversations[conv_key] = history[-self.MAX_HISTORY_MESSAGES:]
                history = self._conversations[conv_key]

            formatted = []
            for msg in history:
                if msg["role"] == "user":
                    formatted.append(f"{msg.get('name', 'User')}: {msg['content']}")
                else:
                    formatted.append(f"Assistant: {msg['content']}")
            conversation_text = "\n".join(formatted)

            response = await agent.call_utility_model(
                system=self.CHAT_SYSTEM_PROMPT,
                message=conversation_text,
            )

            history.append({"role": "assistant", "content": response})
            save_history(conv_key, history)  # persist

            return response if isinstance(response, str) else str(response)

        except ImportError:
            chat_id = conv_key.split(":")[0]
            return await self._get_agent_response_http(chat_id, text)

    # ------------------------------------------------------------------
    # Elevated mode: full agent loop with tools
    # ------------------------------------------------------------------

    async def _get_elevated_response(self, conv_key: str, text: str, message, thread_id=None) -> str:
        """Route through the full Agent Zero agent loop."""
        try:
            from agent import AgentContext, AgentContextType, UserMessage
            from initialize import initialize_agent

            context_id = get_context_id(conv_key)
            context = None

            if context_id:
                context = AgentContext.get(context_id)

            if context is None:
                config = initialize_agent()
                context = AgentContext(config=config, type=AgentContextType.USER)
                set_context_id(conv_key, context.id)
                logger.info(f"Created new elevated context {context.id} for key {conv_key}")

            from usr.plugins.telegram.helpers.sanitize import sanitize_content
            safe_text = sanitize_content(text)
            prefixed_text = safe_text

            attachment_paths = []
            config = self._get_config()
            attach_config = config.get("chat_bridge", {}).get("attachments", {})
            max_bytes = attach_config.get("max_size_mb", 20) * 1_048_576

            # Photos
            if message.photo:
                try:
                    import tempfile
                    photo = message.photo[-1]
                    file = await photo.get_file()
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                    await file.download_to_drive(tmp.name)
                    tmp.close()
                    attachment_paths.append(tmp.name)
                    self._temp_files.append(tmp.name)
                except Exception:
                    pass

            # Documents (Track 9)
            if message.document:
                doc = message.document
                if doc.file_size and doc.file_size > max_bytes:
                    await message.reply_text(
                        f"⚠️ File too large ({doc.file_size // 1_048_576}MB). Max: {max_bytes // 1_048_576}MB."
                    )
                else:
                    try:
                        import tempfile
                        from pathlib import Path as _Path
                        from usr.plugins.telegram.helpers.sanitize import sanitize_filename
                        safe_name = sanitize_filename(doc.file_name or "document")
                        suffix = _Path(safe_name).suffix or ".bin"
                        file = await doc.get_file()
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                        await file.download_to_drive(tmp.name)
                        tmp.close()
                        attachment_paths.append(tmp.name)
                        self._temp_files.append(tmp.name)
                        if not safe_text:
                            prefixed_text = f"[Attached file: {safe_name}]"
                    except Exception as e:
                        logger.warning(f"Document download failed: {e}")

            user_msg = UserMessage(message=prefixed_text, attachments=attachment_paths)
            task = context.communicate(user_msg)
            try:
                result = await task.result()
            finally:
                self._cleanup_temp_files()

            return result if isinstance(result, str) else str(result)

        except ImportError:
            chat_id = conv_key.split(":")[0]
            return await self._get_agent_response_http(chat_id, text)
        except Exception as e:
            logger.error("Elevated mode error: %s", type(e).__name__, exc_info=True)
            set_context_id(conv_key, "")
            raise

    def _cleanup_temp_files(self):
        """Remove temporary image files."""
        remaining = []
        for path in self._temp_files:
            try:
                os.unlink(path)
            except OSError:
                remaining.append(path)
        self._temp_files = remaining

    # ------------------------------------------------------------------
    # HTTP fallback
    # ------------------------------------------------------------------

    async def _get_agent_response_http(self, chat_id: str, text: str) -> str:
        """Fallback: route through Agent Zero's HTTP API."""
        import aiohttp

        config = self._get_config()
        api_port = config.get("chat_bridge", {}).get("api_port", 80)
        api_key = config.get("chat_bridge", {}).get("api_key", "")

        context_id = get_context_id(chat_id) or ""

        async with aiohttp.ClientSession() as session:
            payload = {"message": text, "context_id": context_id}
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["X-API-KEY"] = api_key

            async with session.post(
                f"http://localhost:{api_port}/api/api_message",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return f"Agent API error ({resp.status}): {body}"
                data = await resp.json()

                if data.get("context_id"):
                    set_context_id(chat_id, data["context_id"])

                return data.get("response", "No response from agent.")

    # ------------------------------------------------------------------
    # Response sending
    # ------------------------------------------------------------------

    async def _send_response(self, message, text: str, thread_id=None) -> Optional[int]:
        """Send a response to Telegram. Returns first message_id or None."""
        if not text:
            text = "(No response)"

        from usr.plugins.telegram.helpers.format_telegram import (
            markdown_to_telegram_html,
            split_html_message,
            strip_html,
        )

        html = markdown_to_telegram_html(text)
        chunks = split_html_message(html)
        first_id = None

        for i, chunk in enumerate(chunks):
            sent = await self._send_chunk(message, chunk, i, thread_id=thread_id)
            if i == 0 and sent:
                first_id = sent.message_id

            # Store bot response
            try:
                from usr.plugins.telegram.helpers.message_store import store_message
                raw_msg = {
                    "message_id": sent.message_id,
                    "date": int(sent.date.timestamp()) if sent.date else 0,
                    "chat": {"id": sent.chat_id, "type": sent.chat.type,
                             "title": getattr(sent.chat, "title", ""),
                             "first_name": getattr(sent.chat, "first_name", ""),
                             "username": getattr(sent.chat, "username", "")},
                    "text": sent.text or strip_html(chunk),
                    "from": {
                        "id": self._bot_user.id if self._bot_user else 0,
                        "first_name": self._bot_user.first_name if self._bot_user else "Bot",
                        "username": self._bot_user.username if self._bot_user else "",
                        "is_bot": True,
                    },
                }
                if thread_id:
                    raw_msg["message_thread_id"] = thread_id
                store_message(str(sent.chat_id), raw_msg)
            except Exception:
                pass

        return first_id

    async def _send_chunk(self, message, html_chunk: str, index: int, thread_id=None):
        """Send one chunk as HTML, falling back to plain text on parse error."""
        from telegram.error import BadRequest
        from usr.plugins.telegram.helpers.format_telegram import strip_html

        kwargs = {"parse_mode": "HTML"}
        if thread_id:
            kwargs["message_thread_id"] = thread_id

        if index == 0:
            try:
                return await message.reply_text(html_chunk, **kwargs)
            except BadRequest:
                plain_kwargs = {}
                if thread_id:
                    plain_kwargs["message_thread_id"] = thread_id
                return await message.reply_text(strip_html(html_chunk), **plain_kwargs)
        else:
            try:
                return await message.chat.send_message(html_chunk, **kwargs)
            except BadRequest:
                plain_kwargs = {}
                if thread_id:
                    plain_kwargs["message_thread_id"] = thread_id
                return await message.chat.send_message(strip_html(html_chunk), **plain_kwargs)


    # ------------------------------------------------------------------
    # Voice transcription
    # ------------------------------------------------------------------

    async def _transcribe_voice(self, message) -> Optional[str]:
        """Download and transcribe a voice/audio message. Returns transcript or None."""
        try:
            import tempfile
            voice = message.voice or message.audio
            if not voice:
                return None

            config = self._get_config()
            voice_cfg = config.get("chat_bridge", {}).get("voice", {})
            max_duration = voice_cfg.get("max_duration_seconds", 300)
            duration = getattr(voice, "duration", 0)
            if duration and duration > max_duration:
                logger.debug(f"Voice message too long ({duration}s > {max_duration}s), skipping")
                return None

            suffix = ".ogg" if message.voice else ".mp3"
            tg_file = await voice.get_file()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                await tg_file.download_to_drive(tmp.name)
                tmp_path = tmp.name
            self._temp_files.append(tmp_path)

            # Try A0 utility model with audio attachment.
            # Use a throwaway context and explicitly remove it afterwards to
            # prevent context accumulation (M-5 fix).
            from agent import AgentContext, AgentContextType
            from initialize import initialize_agent
            a0_config = initialize_agent()
            transcribe_ctx = AgentContext(config=a0_config, type=AgentContextType.USER)
            try:
                transcript = await transcribe_ctx.agent0.call_utility_model(
                    system="Transcribe the following audio. Return ONLY the transcribed text, nothing else.",
                    message="[Audio file attached — please transcribe]",
                    attachments=[tmp_path],
                )
            finally:
                try:
                    AgentContext.remove(transcribe_ctx.id)
                except Exception:
                    pass
            return transcript.strip() if transcript else None
        except Exception as e:
            logger.warning(f"Voice transcription failed: {type(e).__name__}: {e}")
            return None

    # ------------------------------------------------------------------
    # Inline keyboard callback handler
    # ------------------------------------------------------------------

    async def _on_callback_query(self, update, context_obj):
        """Handle inline keyboard button taps (Track 4)."""
        query = update.callback_query
        if not query:
            return

        user_id = str(query.from_user.id)
        chat_id = str(query.message.chat_id)
        data = query.data or ""

        # Always answer immediately to clear the loading spinner
        try:
            await query.answer()
        except Exception:
            pass

        # Check allowed users
        config = self._get_config()
        allowed_users = config.get("chat_bridge", {}).get("allowed_users", [])
        if allowed_users and user_id not in [str(u) for u in allowed_users]:
            return

        message_id = query.message.message_id
        approval_key = f"{chat_id}:{message_id}"

        pending = self._pending_approvals.get(approval_key)
        if pending is not None:
            # Security: only the user who triggered the approval can resolve it.
            # This prevents any other chat member from approving elevated actions.
            requester = pending.get("requester_user_id")
            if requester and user_id != requester:
                try:
                    await query.answer("You are not authorized to respond to this approval.")
                except Exception:
                    pass
                return

            future: asyncio.Future = pending.get("future")
            if future and not future.done():
                future.set_result(data)
            self._pending_approvals.pop(approval_key, None)

            outcome_text = pending.get("message_text", "")
            if data in ("approve",) or data.startswith("approve:"):
                outcome_text += "\n\n✅ <b>Approved</b>"
            else:
                outcome_text += "\n\n❌ <b>Rejected</b>"

            try:
                await query.edit_message_text(outcome_text, parse_mode="HTML")
            except Exception:
                pass
        else:
            # No pending approval — acknowledge with a note
            try:
                await query.edit_message_reply_markup(reply_markup={"inline_keyboard": []})
            except Exception:
                pass

    async def request_approval(
        self,
        chat_id: str,
        action_description: str,
        timeout: float = 120.0,
        thread_id: Optional[int] = None,
        requester_user_id: Optional[str] = None,
    ) -> bool:
        """Send approval request buttons and wait for user response. Returns True if approved."""
        from usr.plugins.telegram.helpers.button_builder import approval_buttons
        from usr.plugins.telegram.helpers.format_telegram import markdown_to_telegram_html

        text = (
            "🔐 <b>Action Approval Required</b>\n\n"
            f"{markdown_to_telegram_html(action_description)}\n\n"
            "<i>Tap Approve to allow Agent Zero to proceed.</i>"
        )
        buttons = approval_buttons()

        try:
            from usr.plugins.telegram.helpers.telegram_client import TelegramClient
            client = TelegramClient(self.bot_token)
            try:
                sent = await client.send_message_with_buttons(
                    chat_id, text, buttons,
                    parse_mode="HTML",
                    message_thread_id=thread_id,
                )
            finally:
                await client.close()

            message_id = sent["message_id"]
            approval_key = f"{chat_id}:{message_id}"
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            self._pending_approvals[approval_key] = {
                "future": future,
                "message_text": text,
                "requester_user_id": requester_user_id,  # None = any allowed user may respond
            }

            try:
                result = await asyncio.wait_for(future, timeout=timeout)
                return result == "approve" or str(result).startswith("approve:")
            except asyncio.TimeoutError:
                self._pending_approvals.pop(approval_key, None)
                return False
        except Exception as e:
            logger.error(f"request_approval failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Edited message handler
    # ------------------------------------------------------------------

    async def _on_edited_message(self, update, context_obj):
        """Re-process an edited user message (Track 5)."""
        message = update.edited_message
        if not message or not message.text:
            return

        config = self._get_config()
        if not config.get("chat_bridge", {}).get("handle_edited_messages", True):
            return

        # Only re-process recent edits
        window = config.get("chat_bridge", {}).get("edited_message_window", 600)
        if time.time() - message.date.timestamp() > window:
            return

        chat_id = str(message.chat_id)

        # Delete the original bot reply if we have it tracked
        orig_id = message.message_id
        tracked_reply = self._sent_replies.pop(f"{chat_id}:{orig_id}", None)
        if tracked_reply:
            try:
                await context_obj.bot.delete_message(chat_id, tracked_reply)
            except Exception:
                pass

        # Swap edited_message into update.message for reuse of _on_message
        class _FakeUpdate:
            def __init__(self, msg):
                self.message = msg
                self.edited_message = None
                self.callback_query = None
        fake = _FakeUpdate(message)
        await self._on_message(fake, context_obj)

    # ------------------------------------------------------------------
    # Forum topic handler
    # ------------------------------------------------------------------

    async def _on_forum_topic_created(self, update, context_obj):
        """Handle new forum topic creation (Track 3c)."""
        message = update.message
        if not message:
            return

        topic_info = getattr(message, "forum_topic_created", None)
        if not topic_info:
            return

        chat_id = str(message.chat_id)
        thread_id = message.message_thread_id
        topic_name = getattr(topic_info, "name", f"Topic {thread_id}")
        topic_key = _topic_key(chat_id, thread_id)

        config = self._get_config()
        if not config.get("supergroups", {}).get("auto_context_on_new_topic", True):
            return

        # Don't duplicate if already mapped
        if get_topic_project(topic_key):
            return

        logger.info(f"New forum topic: {topic_name} (key={topic_key})")

        # Create a new A0 context for this topic
        try:
            from agent import AgentContext, AgentContextType
            from initialize import initialize_agent
            a0_config = initialize_agent()
            context = AgentContext(config=a0_config, type=AgentContextType.USER)
            set_context_id(topic_key, context.id)
            set_topic_project(topic_key, context.id, topic_name, auto_created=True)
            logger.info(f"Created context {context.id} for topic {topic_key}")
        except Exception as e:
            logger.warning(f"Could not create context for topic {topic_key}: {e}")
            set_topic_project(topic_key, topic_key, topic_name, auto_created=True)

        # Optionally react with 🎉
        reactions_cfg = config.get("reactions", {})
        if reactions_cfg.get("enabled", True):
            asyncio.create_task(_safe_react(
                context_obj.bot, chat_id, message.message_id, "🎉", config
            ))

    # ------------------------------------------------------------------
    # Bot command handlers (Track 6)
    # ------------------------------------------------------------------

    async def _cmd_auth(self, update, context_obj):
        """Handle /auth <key> command."""
        text = update.message.text or ""
        update.message.text = text.replace("/auth", "!auth", 1)
        await self._handle_auth_command(update, context_obj)

    async def _cmd_deauth(self, update, context_obj):
        update.message.text = "!deauth"
        await self._handle_auth_command(update, context_obj)

    async def _cmd_status(self, update, context_obj):
        update.message.text = "!bridge-status"
        await self._handle_auth_command(update, context_obj)

    async def _cmd_help(self, update, context_obj):
        config = self._get_config()
        elevated_enabled = config.get("chat_bridge", {}).get("allow_elevated", False)
        user_id = str(update.message.from_user.id)
        chat_id = str(update.message.chat_id)
        is_elevated = self._is_elevated(user_id, chat_id)
        mode = "Elevated (full agent access)" if is_elevated else "Restricted (chat only)"
        elev_hint = "\n/auth &lt;key&gt; — Elevate to full Agent Zero access" if elevated_enabled and not is_elevated else ""
        help_text = (
            f"<b>Telegram Bridge — Available Commands</b>\n\n"
            f"Current mode: <b>{mode}</b>\n"
            f"{elev_hint}\n"
            f"/deauth — End elevated session\n"
            f"/status — Show session mode and expiry\n"
            f"/newcontext — Clear conversation history\n"
            f"/cancel — Cancel current in-progress task\n"
            f"/help — Show this message"
        )
        await update.message.reply_text(help_text, parse_mode="HTML")

    async def _cmd_newcontext(self, update, context_obj):
        chat_id = str(update.message.chat_id)
        thread_id = getattr(update.message, "message_thread_id", None)
        key = _topic_key(chat_id, thread_id)
        self._conversations.pop(key, None)
        from usr.plugins.telegram.helpers.conversation_store import clear_history
        clear_history(key)
        set_context_id(key, "")
        await update.message.reply_text("🔄 Conversation reset. Starting fresh.")

    async def _cmd_cancel(self, update, context_obj):
        chat_id = str(update.message.chat_id)
        thread_id = getattr(update.message, "message_thread_id", None)
        key = _topic_key(chat_id, thread_id)
        self._cancel_requested[key] = True
        await update.message.reply_text("⚠️ Cancel requested. Current task will stop at the next checkpoint.")


def _split_message(content: str, max_length: int = 4096) -> list[str]:
    if len(content) <= max_length:
        return [content]
    chunks = []
    while content:
        if len(content) <= max_length:
            chunks.append(content)
            break
        split_at = content.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = content.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(content[:split_at])
        content = content[split_at:].lstrip("\n")
    return chunks


def _is_bot_alive() -> bool:
    """Check if the bot instance and its dedicated thread are actually alive."""
    if _bot_instance is None:
        return False
    if not _bot_instance._running:
        return False
    if _bot_thread is None or not _bot_thread.is_alive():
        return False
    return True


def _cleanup_dead_bot():
    """Clean up singleton refs if the bot/thread has died."""
    global _bot_instance, _bot_thread, _bot_loop
    if not _is_bot_alive():
        _bot_instance = None
        _bot_thread = None
        _bot_loop = None


def _run_bot_in_thread(bot: ChatBridgeBot, ready_event: threading.Event):
    """Run the bot in a dedicated thread with its own event loop."""
    global _bot_instance, _bot_thread, _bot_loop, _fatal_error, _fatal_error_type

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _bot_loop = loop

    bot._ready_event = ready_event

    try:
        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters
        except ModuleNotFoundError:
            logger.warning("python-telegram-bot not found, installing...")
            import subprocess, sys
            python = "/opt/venv-a0/bin/python3" if os.path.isfile("/opt/venv-a0/bin/python3") else sys.executable
            subprocess.run([python, "-m", "pip", "install", "python-telegram-bot>=21.0,<22"], capture_output=True, check=True)
            from telegram.ext import ApplicationBuilder, MessageHandler, filters

        import telegram.error as tg_error
        from telegram.ext import CallbackQueryHandler, CommandHandler

        app = ApplicationBuilder().token(bot.bot_token).build()
        bot._application = app

        # Register message handler
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot._on_message))
        # Also handle commands starting with ! (auth commands)
        app.add_handler(MessageHandler(filters.Regex(r'^!'), bot._on_message))
        # Handle photo and document messages (elevated mode attachments)
        app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, bot._on_message))
        # Handle voice and audio messages (Track 8)
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, bot._on_message))

        # Callback queries (inline button taps)
        app.add_handler(CallbackQueryHandler(bot._on_callback_query))

        # Edited messages
        app.add_handler(MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & filters.TEXT,
            bot._on_edited_message,
        ))

        # Forum topic created
        app.add_handler(MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CREATED,
            bot._on_forum_topic_created,
        ))

        # Bot commands
        app.add_handler(CommandHandler("auth",       bot._cmd_auth))
        app.add_handler(CommandHandler("deauth",     bot._cmd_deauth))
        app.add_handler(CommandHandler("status",     bot._cmd_status))
        app.add_handler(CommandHandler("help",       bot._cmd_help))
        app.add_handler(CommandHandler("newcontext", bot._cmd_newcontext))
        app.add_handler(CommandHandler("cancel",     bot._cmd_cancel))

        bot._running = True

        async def _start():
            """Initialise, run, and tear down the PTB Application.

            Uses a try/finally so app.shutdown() is *always* called — this
            closes underlying aiohttp / httpx client sessions and prevents the
            "Unclosed client session" warnings that appear when the bot exits
            with an error (e.g. InvalidToken) before the normal shutdown path.
            """
            app_initialized = False
            app_started = False
            try:
                await app.initialize()
                app_initialized = True

                me = await app.bot.get_me()
                bot._bot_user = me
                logger.info("Chat bridge connected as @%s (ID: %s)", me.username, me.id)

                # Register bot commands with Telegram.
                # PTB v21 accepts (command, description) tuples directly.
                # Note: Telegram rejects descriptions containing < or > — keep
                # descriptions to plain ASCII with no HTML-like characters.
                try:
                    await app.bot.set_my_commands(
                        [(c["command"], c["description"]) for c in BRIDGE_COMMANDS]
                    )
                    logger.info("Registered %d bot commands", len(BRIDGE_COMMANDS))
                except Exception as e:
                    logger.warning(
                        "Could not register bot commands: %s: %s",
                        type(e).__name__, e, exc_info=True,
                    )

                # Drop any active webhook or lingering getUpdates session from a
                # previous run.  This is the standard fix for the
                # "Conflict: terminated by other getUpdates request" error —
                # calling deleteWebhook forces Telegram to close the old session
                # before we open a new long-poll connection.
                try:
                    await app.bot.delete_webhook(drop_pending_updates=True)
                    logger.debug("delete_webhook: cleared any lingering session.")
                except Exception as _dw_exc:
                    logger.debug("delete_webhook skipped: %s", _dw_exc)

                await app.start()
                app_started = True

                ready_event.set()
                await app.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=[
                        "message", "edited_message", "callback_query",
                        "message_reaction", "forum_topic_created",
                        "forum_topic_edited", "forum_topic_closed", "forum_topic_reopened",
                    ],
                )
                # Keep running until _running is cleared by stop_chat_bridge()
                while bot._running:
                    await asyncio.sleep(1)
                await app.updater.stop()

            finally:
                # Always shut down the application so that the underlying HTTP
                # client sessions (aiohttp / httpx) are properly closed.
                # Calling stop/shutdown on an un-started app is safe — PTB
                # checks internal state before each step.
                try:
                    if app_started:
                        await app.stop()
                except Exception as _stop_exc:
                    logger.debug("app.stop() during cleanup: %s", _stop_exc)
                try:
                    if app_initialized:
                        await app.shutdown()
                except Exception as _sd_exc:
                    logger.debug("app.shutdown() during cleanup: %s", _sd_exc)

        loop.run_until_complete(_start())

    except Exception as e:
        # Classify the error so callers (watchdog, status API) can react
        # appropriately rather than blindly retrying.
        import telegram.error as tg_error  # re-import safe inside except
        if isinstance(e, tg_error.InvalidToken):
            _fatal_error = (
                f"Bot token is invalid or has been revoked ({e}). "
                "Update the token in Settings → External Services → Telegram Integration "
                "and click Start Bridge (or restart the agent)."
            )
            _fatal_error_type = "token"
            logger.error(
                "FATAL — bot token invalid/revoked. The bridge will NOT restart "
                "automatically. Update the token in plugin settings and start the bridge "
                "again. (Raw error: %s)", e,
            )
        elif isinstance(e, tg_error.NetworkError):
            logger.error(
                "Chat bridge lost network connectivity: %s: %s — "
                "the watchdog will attempt to restart.",
                type(e).__name__, e,
            )
        elif isinstance(e, tg_error.TimedOut):
            logger.error(
                "Chat bridge timed out communicating with Telegram: %s — "
                "the watchdog will attempt to restart.",
                e,
            )
        elif isinstance(e, ValueError) and "token" in str(e).lower():
            _fatal_error = (
                f"Bot token appears malformed: {e}. "
                "Check the token in plugin settings."
            )
            _fatal_error_type = "token"
            logger.error("FATAL — malformed bot token: %s", e)
        else:
            logger.error(
                "Chat bridge bot exited with unexpected error: %s: %s",
                type(e).__name__, e, exc_info=True,
            )
    finally:
        logger.info("Chat bridge bot thread ending, cleaning up singleton")
        bot._running = False
        ready_event.set()  # Unblock caller if startup never completed
        _bot_instance = None
        _bot_thread = None
        _bot_loop = None
        try:
            loop.close()
        except Exception:
            pass


async def start_chat_bridge(bot_token: str) -> ChatBridgeBot:
    """Start the chat bridge bot in a dedicated background thread.

    A threading.Lock prevents concurrent calls from racing past the
    _bot_instance guard and spawning duplicate getUpdates sessions,
    which Telegram rejects with a 409 Conflict error.

    Any previous fatal error (e.g. invalid token) is cleared here so that
    updating the token in settings and clicking Start Bridge recovers without
    requiring an agent restart.
    """
    global _bot_instance, _bot_thread, _bot_loop, _fatal_error, _fatal_error_type

    if not bot_token or not bot_token.strip():
        raise ValueError("Cannot start chat bridge: bot token is empty or not configured.")

    # Clear any previous fatal error — the caller may have updated the token.
    _fatal_error = None
    _fatal_error_type = None

    with _start_lock:
        _cleanup_dead_bot()

        if _bot_instance and _is_bot_alive():
            return _bot_instance

        # Force-close any leftover instance whose thread has already died
        if _bot_instance:
            _bot_instance._running = False
            _bot_instance = None
            _bot_thread = None
            _bot_loop = None

        bot = ChatBridgeBot(bot_token)
        _bot_instance = bot

        ready_event = threading.Event()
        thread = threading.Thread(
            target=_run_bot_in_thread,
            args=(bot, ready_event),
            daemon=True,
            name="telegram-chat-bridge",
        )
        _bot_thread = thread
        thread.start()

    # Wait outside the lock so stop_chat_bridge can acquire it if needed
    ready_event.wait(timeout=35)

    if not bot._running:
        logger.warning("Bot started but may not be fully ready yet")

    return bot


async def stop_chat_bridge():
    """Stop the chat bridge bot and wait for its thread to fully exit.

    Waiting for thread death ensures the PTB application has completed
    app.updater.stop() → app.stop() → app.shutdown() before we return,
    so any subsequent start_chat_bridge() call opens a fresh getUpdates
    session rather than racing with the closing one.
    """
    global _bot_instance, _bot_thread, _bot_loop

    if _bot_instance:
        _bot_instance._running = False

    # Snapshot to local to guard against concurrent _cleanup_dead_bot() setting
    # _bot_thread = None between the is_alive() check and the subsequent call.
    thread = _bot_thread
    if thread and thread.is_alive():
        thread.join(timeout=15)
        if thread.is_alive():
            logger.warning("Bridge thread did not exit within 15 s; proceeding anyway.")

    _bot_instance = None
    _bot_thread = None
    _bot_loop = None


def is_bridge_polling() -> bool:
    """Check if the bridge is actively polling getUpdates.

    Tools MUST check this before calling getUpdates themselves, because
    concurrent getUpdates calls to the same bot token cause a Conflict error
    that crashes the bridge's polling loop.
    """
    return _is_bot_alive()


def get_bot_status() -> dict:
    """Get current bot status."""
    _cleanup_dead_bot()

    if _bot_instance is None:
        # Include fatal error detail so callers (WebUI, watchdog) can surface it
        base = {"running": False}
        if _fatal_error:
            base["status"] = "error"
            base["error"] = _fatal_error
            base["error_type"] = _fatal_error_type or "unknown"
        else:
            base["status"] = "stopped"
        return base
    if not _bot_instance._running:
        return {"running": False, "status": "stopped"}
    if _bot_thread and not _bot_thread.is_alive():
        return {"running": False, "status": "crashed"}
    if _bot_instance._bot_user:
        user = _bot_instance._bot_user
        return {
            "running": True,
            "status": "connected",
            "user": f"@{user.username}" if user.username else user.first_name,
            "user_id": str(user.id),
            "topic_count": len(get_topic_map()),
            # Used by watchdog to detect a running-but-frozen bridge
            "last_activity_ts": _bot_instance._last_activity_ts,
            "restart_count": getattr(_bot_instance, "_restart_count", 0),
            "webhook_mode": False,  # polling mode; webhook handler sets True
        }
    return {
        "running": True,
        "status": "connecting",
        "last_activity_ts": _bot_instance._last_activity_ts,
    }


def get_bridge_application():
    """Return the running python-telegram-bot Application instance, or None.

    Used by the webhook endpoint to feed incoming updates to the dispatcher
    without going through getUpdates polling.
    """
    if _bot_instance is not None and _bot_instance._application is not None:
        return _bot_instance._application
    return None
