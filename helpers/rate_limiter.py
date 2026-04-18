"""Token bucket rate limiter for Telegram Bot API calls.

Telegram rate limits:
  - 30 messages/second global
  - 1 message/second per chat
  - ~20 message edits/minute per message (enforced by debounce, not this limiter)

Usage:
    limiter = TelegramRateLimiter()
    await limiter.acquire("global")           # global rate
    await limiter.acquire(f"chat:{chat_id}")  # per-chat rate
"""

import asyncio
import time
import logging
from typing import Optional

logger = logging.getLogger("telegram_rate_limiter")


class TelegramRateLimiter:
    """Token bucket rate limiter with per-key buckets."""

    def __init__(self):
        # (rate, per_seconds) defaults for known key prefixes
        self._configs: dict[str, tuple[float, float]] = {
            "global":  (30.0, 1.0),   # 30 per second
            "chat":    (1.0,  1.0),   # 1 per second per chat
            "edit":    (20.0, 60.0),  # 20 per minute per message
            "react":   (10.0, 1.0),   # 10 per second (generous)
        }
        self._tokens: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_config(self, key: str) -> tuple[float, float]:
        """Get (rate, per_seconds) for a bucket key."""
        prefix = key.split(":")[0]
        return self._configs.get(prefix, (10.0, 1.0))  # safe default

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def acquire(self, key: str = "global", tokens: float = 1.0) -> None:
        """Wait until `tokens` are available for the given bucket key.

        Key formats:
          "global"                    — global bot rate
          "chat:{chat_id}"            — per-chat message rate
          "edit:{chat_id}:{msg_id}"   — per-message edit rate
          "react:{chat_id}"           — per-chat reaction rate
        """
        rate, per = self._get_config(key)
        async with self._get_lock(key):
            now = time.monotonic()
            last = self._last.get(key, now)
            elapsed = now - last
            # Refill tokens proportional to elapsed time
            current = min(rate, self._tokens.get(key, rate) + elapsed * (rate / per))
            if current >= tokens:
                self._tokens[key] = current - tokens
                self._last[key] = now
                return
            # Need to wait
            wait = (tokens - current) * (per / rate)
            logger.debug(f"Rate limiter: waiting {wait:.2f}s for key={key}")
            await asyncio.sleep(wait)
            self._tokens[key] = 0.0
            self._last[key] = time.monotonic()

    def try_acquire(self, key: str = "global", tokens: float = 1.0) -> bool:
        """Non-blocking attempt. Returns True if tokens were available."""
        rate, per = self._get_config(key)
        now = time.monotonic()
        last = self._last.get(key, now)
        elapsed = now - last
        current = min(rate, self._tokens.get(key, rate) + elapsed * (rate / per))
        if current >= tokens:
            self._tokens[key] = current - tokens
            self._last[key] = now
            return True
        return False


# Module-level singleton shared across bridge and streaming
_default_limiter: Optional[TelegramRateLimiter] = None


def get_rate_limiter() -> TelegramRateLimiter:
    """Get or create the module-level rate limiter singleton."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = TelegramRateLimiter()
    return _default_limiter
