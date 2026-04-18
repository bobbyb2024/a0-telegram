"""Persistent conversation history for the chat bridge.

Separate from message_store.py (which stores raw Telegram messages).
This stores the summarised conversation turns passed to the LLM in
restricted mode (call_utility_model). Persists across bridge restarts.

Key format:
  Plain chat:    "{chat_id}"
  Topic thread:  "{chat_id}:topic:{thread_id}"
  Per-user:      "{chat_id}:user:{user_id}"
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("conversation_store")

# Protects all load/modify/save cycles. The bridge loop and A0 tool calls
# may run in different threads; this lock prevents race-condition corruption.
_history_lock = threading.Lock()

MAX_HISTORY_PER_CHAT = 20


def _store_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / "conversation_history.json",
        Path("/a0/usr/plugins/telegram/data/conversation_history.json"),
        Path("/a0/plugins/telegram/data/conversation_history.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    path = candidates[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_all() -> dict:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"conversation_store: load failed: {e}")
        return {}


def _save_all(data: dict):
    """Write atomically with restrictive permissions."""
    try:
        from usr.plugins.telegram.helpers.sanitize import secure_write_json
        secure_write_json(_store_path(), data)
    except Exception as e:
        logger.warning(f"conversation_store: save failed: {e}")


def load_history(chat_key: str) -> list[dict]:
    """Load conversation history for a chat/topic/user key.

    Returns a list of {role, content, name?, timestamp?} dicts.
    """
    try:
        with _history_lock:
            return _load_all().get(str(chat_key), [])
    except Exception:
        return []


def save_history(chat_key: str, history: list[dict]):
    """Persist conversation history (trimmed to MAX_HISTORY_PER_CHAT)."""
    try:
        with _history_lock:
            data = _load_all()
            trimmed = list(history)[-MAX_HISTORY_PER_CHAT:]
            data[str(chat_key)] = trimmed
            _save_all(data)
    except Exception as e:
        logger.warning(f"conversation_store: save_history failed for {chat_key}: {e}")


def append_turn(chat_key: str, role: str, content: str, name: Optional[str] = None):
    """Append one turn to persistent history (load -> append -> save, atomic)."""
    try:
        with _history_lock:
            data = _load_all()
            history = list(data.get(str(chat_key), []))
            entry: dict = {
                "role": role,
                "content": content,
                "timestamp": int(time.time()),
            }
            if name:
                entry["name"] = name
            history.append(entry)
            trimmed = history[-MAX_HISTORY_PER_CHAT:]
            data[str(chat_key)] = trimmed
            _save_all(data)
    except Exception as e:
        logger.warning(f"conversation_store: append_turn failed for {chat_key}: {e}")


def clear_history(chat_key: str):
    """Clear persisted history for a chat (used by /newcontext command)."""
    try:
        with _history_lock:
            data = _load_all()
            data.pop(str(chat_key), None)
            _save_all(data)
    except Exception as e:
        logger.warning(f"conversation_store: clear_history failed for {chat_key}: {e}")


def get_all_chat_keys() -> list[str]:
    """Return all chat keys that have stored history."""
    try:
        return list(_load_all().keys())
    except Exception:
        return []
