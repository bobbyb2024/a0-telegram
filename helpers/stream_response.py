"""Progressive message streaming for the Telegram chat bridge.

Instead of sending a single complete reply, this module:
  1. Sends a placeholder message ("…")
  2. Edits it progressively as text accumulates
  3. Does a final edit with full HTML formatting

Streaming modes:
  word       — emit after each whitespace-delimited token
  sentence   — emit after sentence-ending punctuation (. ! ? …) + space/EOL
  paragraph  — emit after double newline

Rate limiting: min EDIT_INTERVAL_MS between edits (Telegram ~20 edits/min limit).
"""

import asyncio
import logging
import re
import time
from typing import AsyncGenerator, Optional

logger = logging.getLogger("telegram_stream_response")

# Minimum milliseconds between message edits
DEFAULT_EDIT_INTERVAL_MS = 1500
DEFAULT_PLACEHOLDER = "…"

# Sentence boundary: punctuation followed by space or end of string
_SENTENCE_END_RE = re.compile(r'(?<=[.!?…])\s+|(?<=[.!?…])$')
# Paragraph boundary
_PARAGRAPH_END_RE = re.compile(r'\n\n+')
# Word boundary
_WORD_END_RE = re.compile(r'\s+')


def _split_by_mode(text: str, mode: str) -> list[str]:
    """Split accumulated text into chunks by streaming mode."""
    if mode == "word":
        parts = _WORD_END_RE.split(text)
    elif mode == "sentence":
        # Split on sentence boundaries but keep the punctuation
        parts = re.split(r'(?<=[.!?…])\s+', text)
    elif mode == "paragraph":
        parts = _PARAGRAPH_END_RE.split(text)
    else:
        parts = [text]
    return [p for p in parts if p.strip()]


async def stream_text_to_telegram(
    bot,                            # python-telegram-bot Bot instance
    chat_id: str,
    reply_to_message_id: int,
    full_text: str,
    mode: str = "sentence",
    edit_interval_ms: int = DEFAULT_EDIT_INTERVAL_MS,
    placeholder: str = DEFAULT_PLACEHOLDER,
    message_thread_id: Optional[int] = None,
    store_callback=None,            # callable(chat_id, raw_msg) for message_store
) -> Optional[int]:
    """
    Stream full_text to Telegram by progressively editing a sent message.

    This is POST-PROCESSING streaming: the full text is already known, but we
    deliver it progressively to give a typing-feel UX. True streaming from the
    LLM would call this with an async generator instead.

    Returns the message_id of the final sent/edited message, or None on failure.
    """
    from usr.plugins.telegram.helpers.format_telegram import (
        format_streaming_chunk,
        format_streaming_final,
        split_html_message,
        strip_html,
    )

    edit_interval = edit_interval_ms / 1000.0

    # 1. Send the placeholder
    send_kwargs: dict = {
        "chat_id": chat_id,
        "text": placeholder,
    }
    if message_thread_id:
        send_kwargs["message_thread_id"] = message_thread_id
    if reply_to_message_id:
        send_kwargs["reply_to_message_id"] = reply_to_message_id

    try:
        sent = await bot.send_message(**send_kwargs)
        message_id = sent.message_id
    except Exception as e:
        logger.error(f"stream_response: failed to send placeholder: {e}")
        return None

    # 2. Split the full text by mode and stream edits
    chunks = _split_by_mode(full_text, mode)
    accumulated = ""
    last_edit_time = 0.0

    for i, chunk in enumerate(chunks):
        accumulated += (" " if accumulated and not accumulated.endswith("\n") else "") + chunk
        now = time.monotonic()

        # Only edit if enough time has passed (rate limit) or it's the last chunk
        is_last = (i == len(chunks) - 1)
        if not is_last and (now - last_edit_time) < edit_interval:
            continue

        # Format: plain text with cursor during streaming, full HTML for final
        if is_last:
            edit_html = format_streaming_final(accumulated)
        else:
            edit_html = format_streaming_chunk(accumulated)

        # Telegram edit_message_text
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=edit_html,
                parse_mode="HTML" if is_last else None,
            )
            last_edit_time = time.monotonic()
        except Exception as e:
            err_str = str(e).lower()
            # "message is not modified" is not an error
            if "not modified" in err_str:
                pass
            elif "message to edit not found" in err_str:
                logger.warning("stream_response: message was deleted mid-stream")
                return None
            else:
                logger.warning(f"stream_response: edit failed ({type(e).__name__}): {e}")

        if not is_last:
            # Brief yield to keep the event loop responsive
            await asyncio.sleep(0.01)

    # 3. If response is very long, we may need to split it
    # Check final message length and send overflow as new messages
    final_html = format_streaming_final(full_text)
    chunks_html = split_html_message(final_html)

    if len(chunks_html) > 1:
        # First chunk was already edited above; send remaining as new messages
        for overflow_chunk in chunks_html[1:]:
            try:
                overflow_kwargs: dict = {"chat_id": chat_id, "text": overflow_chunk, "parse_mode": "HTML"}
                if message_thread_id:
                    overflow_kwargs["message_thread_id"] = message_thread_id
                await bot.send_message(**overflow_kwargs)
            except Exception as e:
                # Fall back to plain text
                try:
                    plain = strip_html(overflow_chunk)
                    overflow_kwargs2: dict = {"chat_id": chat_id, "text": plain}
                    if message_thread_id:
                        overflow_kwargs2["message_thread_id"] = message_thread_id
                    await bot.send_message(**overflow_kwargs2)
                except Exception:
                    pass

    # 4. Store the final message (not intermediates)
    if store_callback:
        try:
            raw_msg = {
                "message_id": message_id,
                "date": int(time.time()),
                "chat": {"id": int(chat_id)},
                "text": full_text,
            }
            store_callback(chat_id, raw_msg)
        except Exception:
            pass

    return message_id


async def stream_from_generator(
    bot,
    chat_id: str,
    reply_to_message_id: int,
    generator: AsyncGenerator[str, None],
    mode: str = "sentence",
    edit_interval_ms: int = DEFAULT_EDIT_INTERVAL_MS,
    placeholder: str = DEFAULT_PLACEHOLDER,
    message_thread_id: Optional[int] = None,
    store_callback=None,
) -> Optional[int]:
    """
    True streaming: consumes an async generator of text chunks.
    For future use when A0's communicate() supports streaming.
    Falls back to collecting all chunks then calling stream_text_to_telegram.
    """
    chunks = []
    async for chunk in generator:
        chunks.append(chunk)
    full_text = "".join(chunks)
    return await stream_text_to_telegram(
        bot, chat_id, reply_to_message_id, full_text,
        mode=mode, edit_interval_ms=edit_interval_ms,
        placeholder=placeholder, message_thread_id=message_thread_id,
        store_callback=store_callback,
    )
