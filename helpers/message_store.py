"""Persistent message store for Telegram messages.

The chat bridge (python-telegram-bot polling) consumes getUpdates, making
messages unavailable to tools that also call getUpdates. This store captures
messages as they arrive and makes them available to telegram_read.

Messages are stored per-chat in a JSON file, capped at MAX_MESSAGES_PER_CHAT
to prevent unbounded growth.
"""

import json
import os
import time
from pathlib import Path

MAX_MESSAGES_PER_CHAT = 200

def _store_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / "message_store.json",
        Path("/a0/usr/plugins/telegram/data/message_store.json"),
        Path("/a0/plugins/telegram/data/message_store.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    path = candidates[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_store() -> dict:
    path = _store_path()
    if path.exists():
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_store(store: dict):
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(store, f)
    # Restrict permissions to owner-only (VULN-002 fix)
    os.chmod(path, 0o600)


def store_message(chat_id: str, message: dict):
    """Store a single message from a Telegram update.

    `message` should be the raw Telegram message dict (from update.message).
    """
    store = _load_store()
    chat_key = str(chat_id)

    if chat_key not in store:
        store[chat_key] = []

    # Avoid duplicates by message_id
    msg_id = message.get("message_id")
    existing_ids = {m.get("message_id") for m in store[chat_key]}
    if msg_id in existing_ids:
        return

    store[chat_key].append(message)

    # Cap per-chat storage
    if len(store[chat_key]) > MAX_MESSAGES_PER_CHAT:
        store[chat_key] = store[chat_key][-MAX_MESSAGES_PER_CHAT:]

    _save_store(store)


def store_update(update: dict):
    """Extract and store message from a raw Telegram update dict."""
    msg = update.get("message") or update.get("channel_post")
    if msg and msg.get("chat"):
        chat_id = str(msg["chat"]["id"])
        store_message(chat_id, msg)


def get_messages(chat_id: str, limit: int = 50) -> list:
    """Retrieve stored messages for a chat, most recent last."""
    store = _load_store()
    messages = store.get(str(chat_id), [])
    return messages[-limit:]


def get_all_chats() -> dict:
    """Return a dict of chat_id -> basic info for all chats with stored messages."""
    store = _load_store()
    chats = {}
    for chat_id, messages in store.items():
        if messages:
            last_msg = messages[-1]
            chat_info = last_msg.get("chat", {})
            chats[chat_id] = {
                "title": chat_info.get("title") or chat_info.get("first_name", ""),
                "username": chat_info.get("username", ""),
                "type": chat_info.get("type", "unknown"),
                "message_count": len(messages),
                "last_seen": last_msg.get("date", 0),
            }
    return chats
