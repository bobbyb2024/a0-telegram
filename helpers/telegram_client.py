"""Telegram Bot API client wrapper.

Uses aiohttp for direct REST calls to the Telegram Bot API.
This avoids the overhead of python-telegram-bot's Application for
simple one-shot operations (tools, API handlers).
"""

import asyncio
import aiohttp
import os
from typing import Optional

TELEGRAM_API_BASE = "https://api.telegram.org"

# ---------------------------------------------------------------------------
# Rate-limit classification tables
# ---------------------------------------------------------------------------
# Methods that count against the per-chat *send* bucket (1 msg/s per chat).
_CHAT_RATE_METHODS: frozenset = frozenset({
    "sendMessage",
    "sendPhoto",
    "sendDocument",
    "sendVideo",
    "sendAudio",
    "sendVoice",
    "sendAnimation",
    "sendSticker",
    "sendPoll",
    "sendDice",
    "sendMediaGroup",
    "forwardMessage",
    "copyMessage",
    "sendChatAction",
    "pinChatMessage",
    "unpinChatMessage",
    "createForumTopic",
    "editForumTopic",
    "closeForumTopic",
    "reopenForumTopic",
})

# Methods that count against the per-message *edit* bucket (20 edits/min).
_EDIT_RATE_METHODS: frozenset = frozenset({
    "editMessageText",
    "editMessageCaption",
    "editMessageMedia",
    "editMessageReplyMarkup",
})

# Methods that count against the per-chat *reaction* bucket (10 reacts/s).
_REACT_RATE_METHODS: frozenset = frozenset({
    "setMessageReaction",
})


def get_telegram_config(agent=None):
    """Load Telegram config through the plugin framework with env var overrides."""
    try:
        from helpers import plugins
        config = plugins.get_plugin_config("telegram", agent=agent) or {}
    except Exception:
        config = {}

    # Environment variable overrides file config
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        config.setdefault("bot", {})["token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    return config


class TelegramClient:
    """Lightweight Telegram Bot API REST client."""

    def __init__(self, token: str):
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def from_config(cls, agent=None) -> "TelegramClient":
        config = get_telegram_config(agent)
        token = config.get("bot", {}).get("token")
        if not token:
            raise ValueError(
                "Bot token not configured. Set TELEGRAM_BOT_TOKEN env var "
                "or configure in Telegram plugin settings."
            )
        return cls(token=token)

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AgentZero-TelegramPlugin/1.0",
                }
            )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, api_method: str, **kwargs) -> dict:
        """Make a request to the Telegram Bot API.

        Applies rate limiting before each call:
          - Global bucket (30 req/s) always.
          - Per-chat send bucket (1 msg/s) for chat-targeted methods.
          - Per-message edit bucket (20 edits/min) for editMessage* methods.
          - Per-chat reaction bucket (10 react/s) for setMessageReaction.

        On a 429 RetryAfter response, waits the server-specified duration and
        retries once before raising TelegramAPIError.
        """
        await self._ensure_session()

        # --- Rate limiting ---
        from usr.plugins.telegram.helpers.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()

        # Global limit: applies to every API call
        await limiter.acquire("global")

        # Extract chat_id from the JSON payload (present on most POST calls)
        payload: dict = kwargs.get("json") or {}
        chat_id = payload.get("chat_id") if isinstance(payload, dict) else None

        if chat_id is not None:
            if api_method in _CHAT_RATE_METHODS:
                await limiter.acquire(f"chat:{chat_id}")
            if api_method in _EDIT_RATE_METHODS:
                # Per-message bucket: each unique message_id gets its own 20/min limit,
                # matching Telegram's actual per-message edit rate limit model.
                msg_id = payload.get("message_id", "")
                bucket = f"edit:{chat_id}:{msg_id}" if msg_id else f"edit:{chat_id}"
                await limiter.acquire(bucket)
            if api_method in _REACT_RATE_METHODS:
                await limiter.acquire(f"react:{chat_id}")

        # --- HTTP call with one RetryAfter retry ---
        url = f"{TELEGRAM_API_BASE}/bot{self.token}/{api_method}"
        return await self._do_request(method, url, api_method, **kwargs)

    async def _do_request(
        self, method: str, url: str, api_method: str, **kwargs
    ) -> dict:
        """Execute the HTTP call, retrying once on a 429 RetryAfter response."""
        for attempt in range(2):
            async with self._session.request(method, url, **kwargs) as resp:
                data = await resp.json()

                if data.get("ok"):
                    return data.get("result")

                error_code = data.get("error_code", resp.status)
                description = data.get("description", "Unknown error")

                # Handle Telegram's RetryAfter on the first attempt only
                if error_code == 429 and attempt == 0:
                    retry_after = (
                        data.get("parameters", {}).get("retry_after")
                        or _parse_retry_after(description)
                        or 5
                    )
                    import logging as _log
                    _log.getLogger("telegram_client").warning(
                        "Rate limited by Telegram on %s; retrying after %ss",
                        api_method, retry_after,
                    )
                    await asyncio.sleep(float(retry_after))
                    continue  # retry once

                raise TelegramAPIError(error_code, description, api_method)

        # Should never reach here (loop covers exactly 2 attempts)
        raise TelegramAPIError(0, "Unexpected retry loop exit", api_method)

    async def _get(self, api_method: str, params: dict = None) -> dict:
        kwargs = {}
        if params:
            kwargs["params"] = params
        return await self._request("GET", api_method, **kwargs)

    async def _post(self, api_method: str, data: dict = None) -> dict:
        kwargs = {}
        if data:
            kwargs["json"] = data
        return await self._request("POST", api_method, **kwargs)

    # --- Bot info ---

    async def get_me(self) -> dict:
        return await self._get("getMe")

    # --- Messages ---

    async def send_message(
        self, chat_id: str, text: str,
        parse_mode: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
        message_thread_id: Optional[int] = None,
    ) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        if message_thread_id:
            payload["message_thread_id"] = message_thread_id
        return await self._post("sendMessage", payload)

    async def forward_message(
        self, chat_id: str, from_chat_id: str, message_id: int,
    ) -> dict:
        return await self._post("forwardMessage", {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        })

    async def send_photo(
        self, chat_id: str, photo_url: str,
        caption: Optional[str] = None, parse_mode: Optional[str] = None,
    ) -> dict:
        payload = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return await self._post("sendPhoto", payload)

    async def set_message_reaction(
        self, chat_id: str, message_id: int, emoji: str,
    ) -> dict:
        return await self._post("setMessageReaction", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
        })

    # --- Chat history (getUpdates-based or getChat for info) ---

    async def get_updates(
        self, offset: int = 0, limit: int = 100, timeout: int = 0,
        allowed_updates: list = None,
    ) -> list:
        params = {"offset": offset, "limit": limit, "timeout": timeout}
        if allowed_updates:
            import json
            params["allowed_updates"] = json.dumps(allowed_updates)
        return await self._get("getUpdates", params)

    # --- Chat info ---

    async def get_chat(self, chat_id: str) -> dict:
        return await self._post("getChat", {"chat_id": chat_id})

    async def get_chat_member_count(self, chat_id: str) -> int:
        return await self._post("getChatMemberCount", {"chat_id": chat_id})

    async def get_chat_member(self, chat_id: str, user_id: int) -> dict:
        return await self._post("getChatMember", {
            "chat_id": chat_id, "user_id": user_id,
        })

    async def get_chat_administrators(self, chat_id: str) -> list:
        return await self._post("getChatAdministrators", {"chat_id": chat_id})

    # --- Chat management ---

    async def pin_chat_message(self, chat_id: str, message_id: int) -> bool:
        return await self._post("pinChatMessage", {
            "chat_id": chat_id, "message_id": message_id,
        })

    async def unpin_chat_message(self, chat_id: str, message_id: int) -> bool:
        return await self._post("unpinChatMessage", {
            "chat_id": chat_id, "message_id": message_id,
        })

    async def set_chat_title(self, chat_id: str, title: str) -> bool:
        return await self._post("setChatTitle", {
            "chat_id": chat_id, "title": title,
        })

    async def set_chat_description(self, chat_id: str, description: str) -> bool:
        return await self._post("setChatDescription", {
            "chat_id": chat_id, "description": description,
        })

    # --- Message editing ---

    async def edit_message(
        self, chat_id: str, message_id: int, text: str,
        parse_mode: Optional[str] = None,
    ) -> dict:
        """Edit the text of an existing message."""
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return await self._post("editMessageText", payload)

    async def delete_message(self, chat_id: str, message_id: int) -> bool:
        """Delete a message. Bot can only delete its own messages (or with admin rights)."""
        return await self._post("deleteMessage", {
            "chat_id": chat_id, "message_id": message_id,
        })

    # --- Inline keyboards ---

    async def send_message_with_buttons(
        self,
        chat_id: str,
        text: str,
        buttons: list,
        parse_mode: Optional[str] = None,
        message_thread_id: Optional[int] = None,
    ) -> dict:
        """Send a message with an inline keyboard attached."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"inline_keyboard": buttons},
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if message_thread_id:
            payload["message_thread_id"] = message_thread_id
        return await self._post("sendMessage", payload)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge a callback query (must call within 10 seconds)."""
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        return await self._post("answerCallbackQuery", payload)

    async def edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        buttons: Optional[list] = None,
    ) -> dict:
        """Replace or remove the inline keyboard on an existing message. Pass None to remove."""
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": buttons or []},
        }
        return await self._post("editMessageReplyMarkup", payload)

    # --- Bot commands ---

    async def set_my_commands(
        self,
        commands: list,
        scope: Optional[dict] = None,
        language_code: Optional[str] = None,
    ) -> bool:
        """Register bot commands for the command hint menu."""
        payload = {"commands": commands}
        if scope:
            payload["scope"] = scope
        if language_code:
            payload["language_code"] = language_code
        return await self._post("setMyCommands", payload)

    async def delete_my_commands(
        self,
        scope: Optional[dict] = None,
        language_code: Optional[str] = None,
    ) -> bool:
        payload = {}
        if scope:
            payload["scope"] = scope
        if language_code:
            payload["language_code"] = language_code
        return await self._post("deleteMyCommands", payload)

    async def get_my_commands(self) -> list:
        return await self._post("getMyCommands", {})

    # --- Webhook ---

    async def set_webhook(
        self,
        url: str,
        secret_token: Optional[str] = None,
        max_connections: int = 40,
        allowed_updates: Optional[list] = None,
    ) -> bool:
        payload = {"url": url, "max_connections": max_connections}
        if secret_token:
            payload["secret_token"] = secret_token
        if allowed_updates:
            payload["allowed_updates"] = allowed_updates
        return await self._post("setWebhook", payload)

    async def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        return await self._post("deleteWebhook", {
            "drop_pending_updates": drop_pending_updates,
        })

    async def get_webhook_info(self) -> dict:
        return await self._get("getWebhookInfo")

    # --- Polls ---

    async def send_poll(
        self,
        chat_id: str,
        question: str,
        options: list,
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
        message_thread_id: Optional[int] = None,
    ) -> dict:
        """Send a poll. options is a list of strings (max 10, each max 100 chars)."""
        payload = {
            "chat_id": chat_id,
            "question": question[:300],
            "options": [{"text": str(o)[:100]} for o in options[:10]],
            "is_anonymous": is_anonymous,
            "allows_multiple_answers": allows_multiple_answers,
        }
        if message_thread_id:
            payload["message_thread_id"] = message_thread_id
        return await self._post("sendPoll", payload)

    async def stop_poll(self, chat_id: str, message_id: int) -> dict:
        """Close a poll so no more votes can be submitted."""
        return await self._post("stopPoll", {
            "chat_id": chat_id, "message_id": message_id,
        })

    # --- Stickers ---

    async def send_sticker(
        self,
        chat_id: str,
        sticker: str,
        message_thread_id: Optional[int] = None,
    ) -> dict:
        """Send a sticker by file_id or URL."""
        payload = {"chat_id": chat_id, "sticker": sticker}
        if message_thread_id:
            payload["message_thread_id"] = message_thread_id
        return await self._post("sendSticker", payload)

    async def get_sticker_set(self, name: str) -> dict:
        return await self._post("getStickerSet", {"name": name})

    # --- Forum topics ---

    async def create_forum_topic(
        self,
        chat_id: str,
        name: str,
        icon_color: Optional[int] = None,
        icon_custom_emoji_id: Optional[str] = None,
    ) -> dict:
        """Create a forum topic in a supergroup. Returns {message_thread_id, name, ...}"""
        payload = {"chat_id": chat_id, "name": name[:128]}
        if icon_color is not None:
            payload["icon_color"] = icon_color
        if icon_custom_emoji_id:
            payload["icon_custom_emoji_id"] = icon_custom_emoji_id
        return await self._post("createForumTopic", payload)

    async def edit_forum_topic(
        self,
        chat_id: str,
        message_thread_id: int,
        name: Optional[str] = None,
        icon_custom_emoji_id: Optional[str] = None,
    ) -> bool:
        payload = {"chat_id": chat_id, "message_thread_id": message_thread_id}
        if name:
            payload["name"] = name[:128]
        if icon_custom_emoji_id is not None:
            payload["icon_custom_emoji_id"] = icon_custom_emoji_id
        return await self._post("editForumTopic", payload)

    async def close_forum_topic(self, chat_id: str, message_thread_id: int) -> bool:
        return await self._post("closeForumTopic", {
            "chat_id": chat_id, "message_thread_id": message_thread_id,
        })

    async def reopen_forum_topic(self, chat_id: str, message_thread_id: int) -> bool:
        return await self._post("reopenForumTopic", {
            "chat_id": chat_id, "message_thread_id": message_thread_id,
        })

    async def get_forum_topic_icon_stickers(self) -> list:
        """Get custom emoji stickers usable as forum topic icons."""
        return await self._get("getForumTopicIconStickers")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _parse_retry_after(description: str) -> Optional[int]:
    """Extract the retry-after seconds from a Telegram 429 error description.

    Telegram sends messages like: 'Too Many Requests: retry after 30'
    """
    import re
    m = re.search(r"retry after (\d+)", description, re.IGNORECASE)
    return int(m.group(1)) if m else None


class TelegramAPIError(Exception):
    def __init__(self, error_code: int, description: str, method: str):
        self.error_code = error_code
        self.description = description
        self.method = method
        super().__init__(f"Telegram API error {error_code} on {method}: {description}")


def format_messages(messages: list, include_ids: bool = False) -> str:
    """Format Telegram messages into readable text for LLM consumption.

    All external content (usernames, message text, captions, filenames) is
    sanitized to neutralise prompt injection attempts.
    """
    from usr.plugins.telegram.helpers.sanitize import (
        sanitize_content, sanitize_username, sanitize_caption, sanitize_filename,
    )

    lines = []
    for msg in messages:
        sender = msg.get("from", {})
        first_name = sender.get("first_name", "")
        last_name = sender.get("last_name", "")
        username = sanitize_username(
            f"{first_name} {last_name}".strip() or sender.get("username", "Unknown")
        )
        timestamp = ""
        if msg.get("date"):
            import datetime
            dt = datetime.datetime.fromtimestamp(msg["date"], tz=datetime.timezone.utc)
            timestamp = dt.strftime("%Y-%m-%d %H:%M")
        content = sanitize_content(msg.get("text", ""))

        caption_text = ""
        if msg.get("caption"):
            caption_text = f" [Caption: {sanitize_caption(msg['caption'])}]"

        # Photo/document/audio indicators
        media_text = ""
        if msg.get("photo"):
            media_text = " [Photo]"
        elif msg.get("document"):
            doc = msg["document"]
            fname = sanitize_filename(doc.get("file_name", "document"))
            media_text = f" [Document: {fname}]"
        elif msg.get("audio"):
            media_text = " [Audio]"
        elif msg.get("video"):
            media_text = " [Video]"
        elif msg.get("voice"):
            media_text = " [Voice message]"
        elif msg.get("sticker"):
            emoji = msg["sticker"].get("emoji", "")
            media_text = f" [Sticker{': ' + emoji if emoji else ''}]"

        reply_text = ""
        if msg.get("reply_to_message"):
            ref = msg["reply_to_message"]
            ref_sender = ref.get("from", {})
            ref_name = sanitize_username(
                ref_sender.get("first_name", "Unknown")
            )
            reply_text = f" (replying to {ref_name})"

        prefix = f"[{msg.get('message_id', '?')}] " if include_ids else ""
        lines.append(
            f"{prefix}[{timestamp}] {username}{reply_text}: {content}{caption_text}{media_text}"
        )

    return "\n".join(lines)
