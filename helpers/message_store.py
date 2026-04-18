"""Persistent message store for Telegram messages.

The chat bridge (python-telegram-bot polling) consumes getUpdates, making
messages unavailable to tools that also call getUpdates. This store captures
messages as they arrive and makes them available to telegram_read.

Messages are stored per-chat in a JSON file, capped at MAX_MESSAGES_PER_CHAT
to prevent unbounded growth.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

MAX_MESSAGES_PER_CHAT = 200

# Protects all load/mutate/save cycles against concurrent access from the
# bridge thread and the tool-call (main event loop) thread.
_store_lock = threading.Lock()

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
    """Atomically write the store with 0o600 permissions (caller holds _store_lock)."""
    from usr.plugins.telegram.helpers.sanitize import secure_write_json
    secure_write_json(_store_path(), store, indent=None)


def store_message(chat_id: str, message: dict):
    """Store a single message from a Telegram update.

    For forum topic messages (message_thread_id present), stores under
    '{chat_id}:topic:{thread_id}' key as well as the base chat_id key.
    """
    with _store_lock:
        store = _load_store()

        # Determine storage keys
        base_key = str(chat_id)
        thread_id = message.get("message_thread_id")
        topic_key = f"{base_key}:topic:{thread_id}" if thread_id else None

        # Avoid duplicate message_ids
        msg_id = message.get("message_id")

        for key in filter(None, [topic_key, base_key]):
            if key not in store:
                store[key] = []
            existing_ids = {m.get("message_id") for m in store[key]}
            if msg_id in existing_ids:
                if key == base_key:
                    break  # if already in base, skip topic too
                continue
            store[key].append(message)
            if len(store[key]) > MAX_MESSAGES_PER_CHAT:
                store[key] = store[key][-MAX_MESSAGES_PER_CHAT:]

        _save_store(store)


def store_update(update: dict):
    """Extract and store message from a raw Telegram update dict."""
    msg = update.get("message") or update.get("channel_post")
    if msg and msg.get("chat"):
        chat_id = str(msg["chat"]["id"])
        store_message(chat_id, msg)


def get_messages(chat_id: str, limit: int = 50, thread_id: Optional[int] = None) -> list:
    """Retrieve stored messages for a chat, most recent last.

    If thread_id is given, retrieves from the topic-specific key.
    Falls back to filtering from base chat if topic key has no messages.
    """
    with _store_lock:
        store = _load_store()
        if thread_id is not None:
            topic_key = f"{str(chat_id)}:topic:{thread_id}"
            messages = store.get(topic_key, [])
            if messages:
                return messages[-limit:]
            # Fallback: filter from base chat
            all_msgs = store.get(str(chat_id), [])
            filtered = [m for m in all_msgs if m.get("message_thread_id") == thread_id]
            return filtered[-limit:]
        return store.get(str(chat_id), [])[-limit:]


def get_all_chats() -> dict:
    """Return a dict of chat_id -> basic info for all chats with stored messages.
    Includes both base chat keys and topic keys."""
    with _store_lock:
        store = _load_store()
    chats = {}
    for chat_id, messages in store.items():
        if messages:
            last_msg = messages[-1]
            chat_info = last_msg.get("chat", {})
            is_topic = ":topic:" in str(chat_id)
            chats[chat_id] = {
                "title": chat_info.get("title") or chat_info.get("first_name", ""),
                "username": chat_info.get("username", ""),
                "type": "topic" if is_topic else chat_info.get("type", "unknown"),
                "message_count": len(messages),
                "last_seen": last_msg.get("date", 0),
            }
    return chats
