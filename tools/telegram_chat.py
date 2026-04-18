from helpers.tool import Tool, Response
from usr.plugins.telegram.helpers.telegram_client import get_telegram_config
from usr.plugins.telegram.helpers.telegram_bridge import (
    start_chat_bridge,
    stop_chat_bridge,
    get_bot_status,
    add_chat,
    remove_chat,
    get_chat_list,
)
from usr.plugins.telegram.helpers.sanitize import require_auth, validate_chat_id


class TelegramChat(Tool):
    """Manage the Telegram chat bridge — a persistent bot that lets users
    chat with Agent Zero through Telegram."""

    async def execute(self, **kwargs) -> Response:
        config = get_telegram_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        action = self.args.get("action", "status")

        if action == "start":
            return await self._start()
        elif action == "stop":
            return await self._stop()
        elif action == "restart":
            return await self._restart()
        elif action == "add_chat":
            return self._add_chat()
        elif action == "remove_chat":
            return self._remove_chat()
        elif action == "list":
            return self._list_chats()
        elif action == "status":
            return self._status()
        else:
            return Response(
                message=f"Unknown action '{action}'. Use: start, stop, restart, add_chat, remove_chat, list, status.",
                break_loop=False,
            )

    async def _start(self) -> Response:
        """Start the chat bridge bot."""
        config = get_telegram_config(self.agent)
        token = config.get("bot", {}).get("token", "")

        if not token:
            return Response(
                message="Error: Bot token not configured. Set TELEGRAM_BOT_TOKEN or configure in plugin settings.",
                break_loop=False,
            )

        status = get_bot_status()
        if status.get("running") and status.get("status") == "connected":
            return Response(
                message=f"Chat bridge is already running as {status.get('user', 'unknown')}.",
                break_loop=False,
            )

        self.set_progress("Starting chat bridge bot...")
        try:
            bot = await start_chat_bridge(token)
            status = get_bot_status()
            chats = get_chat_list()
            msg = f"Chat bridge started as **{status.get('user', 'unknown')}**."
            if chats:
                msg += f"\nListening in {len(chats)} chat(s)."
            else:
                msg += "\nNo bridge chats configured yet. Use action 'add_chat' to designate a chat."
            return Response(message=msg, break_loop=False)
        except TimeoutError:
            return Response(
                message="Error: Bot failed to connect within 30 seconds. Check your bot token.",
                break_loop=False,
            )
        except Exception as e:
            return Response(message=f"Error starting chat bridge: {type(e).__name__}", break_loop=False)

    async def _stop(self) -> Response:
        """Stop the chat bridge bot."""
        status = get_bot_status()
        if not status.get("running"):
            return Response(message="Chat bridge is not running.", break_loop=False)

        self.set_progress("Stopping chat bridge bot...")
        try:
            await stop_chat_bridge()
            return Response(message="Chat bridge stopped.", break_loop=False)
        except Exception as e:
            return Response(message=f"Error stopping chat bridge: {type(e).__name__}", break_loop=False)

    async def _restart(self) -> Response:
        """Restart the chat bridge bot."""
        self.set_progress("Restarting chat bridge bot...")
        await stop_chat_bridge()

        config = get_telegram_config(self.agent)
        token = config.get("bot", {}).get("token", "")
        if not token:
            return Response(
                message="Error: Bot token not configured.",
                break_loop=False,
            )

        try:
            await start_chat_bridge(token)
            status = get_bot_status()
            return Response(
                message=f"Chat bridge restarted as **{status.get('user', 'unknown')}**.",
                break_loop=False,
            )
        except Exception as e:
            return Response(message=f"Error restarting: {type(e).__name__}", break_loop=False)

    def _add_chat(self) -> Response:
        """Add a chat (or topic) to the bridge."""
        chat_id = self.args.get("chat_id", "")
        label = self.args.get("label", "")
        thread_id = self.args.get("thread_id", "")

        try:
            chat_id = validate_chat_id(chat_id, "chat_id")
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if thread_id:
            try:
                tid = int(thread_id)
            except ValueError:
                return Response(message="Error: thread_id must be an integer.", break_loop=False)
            key = f"{chat_id}:topic:{tid}"
            add_chat(key, label or f"Topic {tid}")
            msg = f"Topic {tid} in chat {chat_id} added to bridge"
            if label:
                msg += f" ({label})"
            msg += ". Messages in this topic thread will be routed to Agent Zero."
        else:
            add_chat(chat_id, label)
            msg = f"Chat {chat_id} added to bridge"
            if label:
                msg += f" ({label})"
            msg += ". Messages in this chat will be routed to Agent Zero's LLM."
        return Response(message=msg, break_loop=False)

    def _remove_chat(self) -> Response:
        """Remove a chat (or topic) from the bridge."""
        chat_id = self.args.get("chat_id", "")
        thread_id = self.args.get("thread_id", "")
        try:
            chat_id = validate_chat_id(chat_id, "chat_id")
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if thread_id:
            try:
                key = f"{chat_id}:topic:{int(thread_id)}"
            except ValueError:
                return Response(message="Error: thread_id must be an integer.", break_loop=False)
            remove_chat(key)
            return Response(message=f"Topic {thread_id} in chat {chat_id} removed from bridge.", break_loop=False)
        else:
            remove_chat(chat_id)
            return Response(
                message=f"Chat {chat_id} removed from bridge.",
                break_loop=False,
            )

    def _list_chats(self) -> Response:
        """List all bridge chats, grouping topics under their parent chat."""
        chats = get_chat_list()
        if not chats:
            return Response(
                message="No bridge chats configured. Use action 'add_chat' to add one.",
                break_loop=False,
            )

        # Separate plain chats from topic keys
        plain_chats = {}
        topic_chats = {}
        for cid, info in chats.items():
            if ":topic:" in cid:
                base, tid = cid.split(":topic:", 1)
                topic_chats.setdefault(base, {})[tid] = info
            else:
                plain_chats[cid] = info

        lines = [f"Bridge chats ({len(chats)} total):"]
        for cid, info in plain_chats.items():
            label = info.get("label", cid)
            added = info.get("added_at", "unknown")
            topics_for_chat = topic_chats.get(cid, {})
            topic_note = f" [{len(topics_for_chat)} topic(s)]" if topics_for_chat else ""
            lines.append(f"  - {label} (ID: {cid}, added: {added}{topic_note})")
            for tid, tinfo in topics_for_chat.items():
                tlabel = tinfo.get("label", f"Topic {tid}")
                lines.append(f"      • {tlabel} (thread_id: {tid})")

        # Topics under chats not in plain_chats
        for base, topics in topic_chats.items():
            if base not in plain_chats:
                lines.append(f"  - Supergroup {base} (topics only):")
                for tid, tinfo in topics.items():
                    tlabel = tinfo.get("label", f"Topic {tid}")
                    lines.append(f"      • {tlabel} (thread_id: {tid})")

        status = get_bot_status()
        if status.get("running"):
            lines.append(f"\nBot status: {status.get('status')} as {status.get('user', '?')}")
        else:
            lines.append("\nBot status: not running")

        return Response(message="\n".join(lines), break_loop=False)

    def _status(self) -> Response:
        """Get chat bridge status."""
        status = get_bot_status()
        chats = get_chat_list()

        if not status.get("running"):
            msg = f"Chat bridge is **not running** (status: {status.get('status', 'stopped')})."
            if chats:
                msg += f"\n{len(chats)} chat(s) configured but bot is offline."
            return Response(message=msg, break_loop=False)

        lines = [
            f"Chat bridge is **{status.get('status')}** as **{status.get('user', '?')}**",
            f"  User ID: {status.get('user_id', '?')}",
            f"  Bridge chats: {len(chats)}",
        ]

        for cid, info in chats.items():
            label = info.get("label", cid)
            lines.append(f"    - {label} (ID: {cid})")

        return Response(message="\n".join(lines), break_loop=False)
