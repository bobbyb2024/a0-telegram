from helpers.tool import Tool, Response
from usr.plugins.telegram.helpers.telegram_client import (
    TelegramClient, TelegramAPIError, get_telegram_config,
)
from usr.plugins.telegram.helpers.sanitize import require_auth, validate_chat_id, validate_image_url


class TelegramSend(Tool):
    """Send messages, photos, reactions, polls, stickers, or manage messages via Telegram bot."""

    async def execute(self, **kwargs) -> Response:
        chat_id = self.args.get("chat_id", "")
        content = self.args.get("content", "")
        reply_to = self.args.get("reply_to", "")
        action = self.args.get("action", "send")
        parse_mode = self.args.get("parse_mode", "")

        try:
            chat_id = validate_chat_id(chat_id)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        config = get_telegram_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        client = TelegramClient.from_config(agent=self.agent)
        try:

            if action == "send":
                if not content:
                    return Response(message="Error: content is required for sending.", break_loop=False)

                thread_id = self.args.get("message_thread_id", "")
                chunks = _split_message(content)
                sent_ids = []
                for i, chunk in enumerate(chunks):
                    ref = int(reply_to) if i == 0 and reply_to else None
                    result = await client.send_message(
                        chat_id=chat_id, text=chunk,
                        parse_mode=parse_mode or None,
                        reply_to_message_id=ref,
                        message_thread_id=int(thread_id) if thread_id else None,
                    )
                    sent_ids.append(str(result.get("message_id", "?")))

                if len(sent_ids) == 1:
                    return Response(message=f"Message sent (ID: {sent_ids[0]}).", break_loop=False)
                return Response(
                    message=f"Message sent in {len(sent_ids)} parts (IDs: {', '.join(sent_ids)}).",
                    break_loop=False,
                )

            elif action == "reply":
                if not content or not reply_to:
                    return Response(
                        message="Error: content and reply_to are required for replying.",
                        break_loop=False,
                    )
                thread_id = self.args.get("message_thread_id", "")
                result = await client.send_message(
                    chat_id=chat_id, text=content,
                    parse_mode=parse_mode or None,
                    reply_to_message_id=int(reply_to),
                    message_thread_id=int(thread_id) if thread_id else None,
                )
                return Response(
                    message=f"Reply sent (ID: {result.get('message_id', '?')}).",
                    break_loop=False,
                )

            elif action == "forward":
                from_chat_id = self.args.get("from_chat_id", "")
                message_id = self.args.get("message_id", "")
                if not from_chat_id or not message_id:
                    return Response(
                        message="Error: from_chat_id and message_id are required for forwarding.",
                        break_loop=False,
                    )
                result = await client.forward_message(
                    chat_id=chat_id, from_chat_id=from_chat_id,
                    message_id=int(message_id),
                )
                return Response(
                    message=f"Message forwarded (ID: {result.get('message_id', '?')}).",
                    break_loop=False,
                )

            elif action == "react":
                emoji = self.args.get("emoji", "")
                message_id = self.args.get("message_id", "")
                if not emoji or not message_id:
                    return Response(
                        message="Error: emoji and message_id required for reactions.",
                        break_loop=False,
                    )
                await client.set_message_reaction(chat_id, int(message_id), emoji)
                return Response(
                    message=f"Reaction {emoji} added to message {message_id}.",
                    break_loop=False,
                )

            elif action == "photo":
                photo_url = self.args.get("photo_url", "")
                if not photo_url:
                    return Response(message="Error: photo_url is required.", break_loop=False)
                # SSRF defense: only allow HTTPS URLs (any host is permitted for
                # agent-sent photos, unlike inbound Telegram CDN URLs which are
                # restricted to api.telegram.org).  Block plain HTTP and
                # non-HTTP schemes (file://, data:, etc.).
                from urllib.parse import urlparse as _urlparse
                parsed_url = _urlparse(photo_url)
                if parsed_url.scheme != "https":
                    return Response(
                        message="Error: photo_url must use HTTPS.",
                        break_loop=False,
                    )
                result = await client.send_photo(
                    chat_id=chat_id, photo_url=photo_url,
                    caption=content or None,
                    parse_mode=parse_mode or None,
                )
                return Response(
                    message=f"Photo sent (ID: {result.get('message_id', '?')}).",
                    break_loop=False,
                )

            elif action == "edit":
                message_id = self.args.get("message_id", "")
                if not content or not message_id:
                    return Response(message="Error: content and message_id required for edit.", break_loop=False)
                result = await client.edit_message(chat_id, int(message_id), content, parse_mode or None)
                return Response(message=f"Message {message_id} edited.", break_loop=False)

            elif action == "delete":
                message_id = self.args.get("message_id", "")
                if not message_id:
                    return Response(message="Error: message_id required for delete.", break_loop=False)
                await client.delete_message(chat_id, int(message_id))
                return Response(message=f"Message {message_id} deleted.", break_loop=False)

            elif action == "send_buttons":
                buttons_raw = self.args.get("buttons", [])
                if not content:
                    return Response(message="Error: content required for send_buttons.", break_loop=False)
                # buttons_raw: [[label, callback_data], ...] or [[[label, data], ...], ...] (rows)
                # Normalise to Telegram format [[{text, callback_data}], ...]
                buttons = _normalise_buttons(buttons_raw)
                thread_id = self.args.get("message_thread_id", "")
                result = await client.send_message_with_buttons(
                    chat_id, content, buttons,
                    parse_mode=parse_mode or None,
                    message_thread_id=int(thread_id) if thread_id else None,
                )
                return Response(message=f"Message with buttons sent (ID: {result.get('message_id', '?')}).", break_loop=False)

            elif action == "poll":
                options = self.args.get("options", [])
                allows_multiple = str(self.args.get("allows_multiple_answers", "false")).lower() == "true"
                is_anonymous = str(self.args.get("is_anonymous", "true")).lower() == "true"
                thread_id = self.args.get("message_thread_id", "")
                if not content or not options:
                    return Response(message="Error: content (question) and options required for poll.", break_loop=False)
                if isinstance(options, str):
                    options = [o.strip() for o in options.split(",") if o.strip()]
                result = await client.send_poll(
                    chat_id, content, options,
                    is_anonymous=is_anonymous,
                    allows_multiple_answers=allows_multiple,
                    message_thread_id=int(thread_id) if thread_id else None,
                )
                return Response(message=f"Poll sent (ID: {result.get('message_id', '?')}).", break_loop=False)

            elif action == "stop_poll":
                message_id = self.args.get("message_id", "")
                if not message_id:
                    return Response(message="Error: message_id required for stop_poll.", break_loop=False)
                await client.stop_poll(chat_id, int(message_id))
                return Response(message=f"Poll {message_id} closed.", break_loop=False)

            elif action == "sticker":
                sticker_id = self.args.get("sticker", "")
                thread_id = self.args.get("message_thread_id", "")
                if not sticker_id:
                    return Response(message="Error: sticker (file_id) required.", break_loop=False)
                result = await client.send_sticker(
                    chat_id, sticker_id,
                    message_thread_id=int(thread_id) if thread_id else None,
                )
                return Response(message=f"Sticker sent (ID: {result.get('message_id', '?')}).", break_loop=False)

            else:
                return Response(
                    message=f"Unknown action '{action}'. Use 'send', 'reply', 'forward', 'react', 'photo', 'edit', 'delete', 'send_buttons', 'poll', 'stop_poll', or 'sticker'.",
                    break_loop=False,
                )

        except TelegramAPIError as e:
            return Response(message=f"Telegram API error: {e}", break_loop=False)
        except Exception as e:
            return Response(message=f"Error sending to Telegram: {type(e).__name__}", break_loop=False)
        finally:
            await client.close()


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


_MAX_CALLBACK_BYTES = 64  # Telegram hard limit for callback_data


def _cap_callback(data: str) -> str:
    """Truncate callback_data to Telegram's 64-byte hard limit (UTF-8 safe)."""
    encoded = data.encode("utf-8")
    if len(encoded) <= _MAX_CALLBACK_BYTES:
        return data
    return encoded[:_MAX_CALLBACK_BYTES].decode("utf-8", errors="ignore")


def _normalise_buttons(raw) -> list:
    """Normalise various button input formats to Telegram's [[{text, callback_data}]] format.

    callback_data values are capped at 64 bytes (Telegram hard limit) to prevent
    silent 400 errors when the agent supplies long identifiers.
    """
    if not raw:
        return []
    result = []
    for row in raw:
        if isinstance(row, list) and row and isinstance(row[0], str):
            # [label, data] pair — single-button row
            data = row[1] if len(row) > 1 else row[0]
            result.append([{"text": row[0], "callback_data": _cap_callback(data)}])
        elif isinstance(row, list):
            # Already a row of button dicts or [label, data] pairs
            normalised_row = []
            for btn in row:
                if isinstance(btn, dict):
                    cb = btn.get("callback_data", "")
                    normalised_row.append({**btn, "callback_data": _cap_callback(cb)} if cb else btn)
                elif isinstance(btn, list) and len(btn) >= 2:
                    normalised_row.append({"text": btn[0], "callback_data": _cap_callback(btn[1])})
            if normalised_row:
                result.append(normalised_row)
    return result
