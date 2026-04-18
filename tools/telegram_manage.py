from helpers.tool import Tool, Response
from usr.plugins.telegram.helpers.telegram_client import (
    TelegramClient, TelegramAPIError, get_telegram_config,
)
from usr.plugins.telegram.helpers.sanitize import require_auth, validate_chat_id, sanitize_chat_title


class TelegramManage(Tool):
    """Manage Telegram chats: pin/unpin messages, set chat title/description, manage forum topics."""

    async def execute(self, **kwargs) -> Response:
        chat_id = self.args.get("chat_id", "")
        action = self.args.get("action", "")
        message_id = self.args.get("message_id", "")
        value = self.args.get("value", "")

        try:
            chat_id = validate_chat_id(chat_id)
        except ValueError as e:
            return Response(message=f"Error: {e}", break_loop=False)

        if not action:
            return Response(
                message="Error: action is required. Use: pin, unpin, set_title, set_description, create_topic, rename_topic, close_topic, reopen_topic, map_topic, unmap_topic, list_topics.",
                break_loop=False,
            )

        config = get_telegram_config(self.agent)
        try:
            require_auth(config)
        except ValueError as e:
            return Response(message=f"Auth error: {e}", break_loop=False)

        try:
            client = TelegramClient.from_config(agent=self.agent)

            if action == "pin":
                if not message_id:
                    return Response(message="Error: message_id is required for pinning.", break_loop=False)
                await client.pin_chat_message(chat_id, int(message_id))
                await client.close()
                return Response(message=f"Message {message_id} pinned in chat {chat_id}.", break_loop=False)

            elif action == "unpin":
                if not message_id:
                    return Response(message="Error: message_id is required for unpinning.", break_loop=False)
                await client.unpin_chat_message(chat_id, int(message_id))
                await client.close()
                return Response(message=f"Message {message_id} unpinned in chat {chat_id}.", break_loop=False)

            elif action == "set_title":
                if not value:
                    return Response(message="Error: value is required for set_title.", break_loop=False)
                safe_title = sanitize_chat_title(value, max_length=255)
                await client.set_chat_title(chat_id, safe_title)
                await client.close()
                return Response(message=f"Chat title set to '{safe_title}'.", break_loop=False)

            elif action == "set_description":
                safe_desc = sanitize_chat_title(value, max_length=255) if value else ""
                await client.set_chat_description(chat_id, safe_desc)
                await client.close()
                msg = "Chat description updated." if safe_desc else "Chat description cleared."
                return Response(message=msg, break_loop=False)

            elif action == "map_topic":
                thread_id = self.args.get("thread_id", "")
                project_id = self.args.get("project_id", "")
                name = self.args.get("name", "")
                if not thread_id:
                    return Response(message="Error: thread_id required for map_topic.", break_loop=False)
                try:
                    tid = int(thread_id)
                except ValueError:
                    return Response(message="Error: thread_id must be an integer.", break_loop=False)
                topic_key = f"{chat_id}:topic:{tid}"
                from usr.plugins.telegram.helpers.telegram_bridge import set_topic_project
                set_topic_project(topic_key, project_id or topic_key, name or f"Topic {tid}")
                await client.close()
                return Response(
                    message=f"Topic {tid} in chat {chat_id} mapped to project '{project_id or topic_key}'.",
                    break_loop=False,
                )

            elif action == "unmap_topic":
                thread_id = self.args.get("thread_id", "")
                if not thread_id:
                    return Response(message="Error: thread_id required for unmap_topic.", break_loop=False)
                try:
                    tid = int(thread_id)
                except ValueError:
                    return Response(message="Error: thread_id must be an integer.", break_loop=False)
                topic_key = f"{chat_id}:topic:{tid}"
                from usr.plugins.telegram.helpers.telegram_bridge import load_chat_state, save_chat_state
                state = load_chat_state()
                state.get("topics", {}).pop(topic_key, None)
                save_chat_state(state)
                await client.close()
                return Response(message=f"Topic {tid} mapping removed.", break_loop=False)

            elif action == "list_topics":
                from usr.plugins.telegram.helpers.telegram_bridge import get_topic_map
                all_topics = get_topic_map()
                chat_topics = {k: v for k, v in all_topics.items() if k.startswith(f"{chat_id}:topic:")}
                await client.close()
                if not chat_topics:
                    return Response(message=f"No topic mappings found for chat {chat_id}.", break_loop=False)
                lines = [f"Topic mappings for {chat_id}:"]
                for key, info in chat_topics.items():
                    tid = key.split(":topic:")[-1]
                    lines.append(f"  thread {tid}: {info.get('name','?')} → {info.get('project_id','?')}")
                return Response(message="\n".join(lines), break_loop=False)

            elif action == "create_topic":
                name = self.args.get("name", "")
                if not name:
                    return Response(message="Error: name required for create_topic.", break_loop=False)
                icon_color = self.args.get("icon_color")
                result = await client.create_forum_topic(
                    chat_id, name,
                    icon_color=int(icon_color) if icon_color else None,
                )
                tid = result.get("message_thread_id")
                await client.close()
                if tid:
                    topic_key = f"{chat_id}:topic:{tid}"
                    from usr.plugins.telegram.helpers.telegram_bridge import set_topic_project
                    set_topic_project(topic_key, topic_key, name)
                return Response(
                    message=f"Forum topic '{name}' created (thread_id: {tid}).",
                    break_loop=False,
                )

            elif action == "rename_topic":
                thread_id = self.args.get("thread_id", "")
                name = self.args.get("name", "")
                if not thread_id or not name:
                    return Response(message="Error: thread_id and name required for rename_topic.", break_loop=False)
                tid = int(thread_id)
                await client.edit_forum_topic(chat_id, tid, name=name)
                # Update local state
                topic_key = f"{chat_id}:topic:{tid}"
                from usr.plugins.telegram.helpers.telegram_bridge import load_chat_state, save_chat_state
                state = load_chat_state()
                if topic_key in state.get("topics", {}):
                    state["topics"][topic_key]["name"] = name
                    save_chat_state(state)
                await client.close()
                return Response(message=f"Topic {tid} renamed to '{name}'.", break_loop=False)

            elif action == "close_topic":
                thread_id = self.args.get("thread_id", "")
                if not thread_id:
                    return Response(message="Error: thread_id required.", break_loop=False)
                await client.close_forum_topic(chat_id, int(thread_id))
                await client.close()
                return Response(message=f"Topic {thread_id} closed.", break_loop=False)

            elif action == "reopen_topic":
                thread_id = self.args.get("thread_id", "")
                if not thread_id:
                    return Response(message="Error: thread_id required.", break_loop=False)
                await client.reopen_forum_topic(chat_id, int(thread_id))
                await client.close()
                return Response(message=f"Topic {thread_id} reopened.", break_loop=False)

            else:
                return Response(
                    message=f"Unknown action '{action}'. Use: pin, unpin, set_title, set_description, create_topic, rename_topic, close_topic, reopen_topic, map_topic, unmap_topic, list_topics.",
                    break_loop=False,
                )

        except TelegramAPIError as e:
            return Response(message=f"Telegram API error: {e}", break_loop=False)
        except Exception as e:
            return Response(message=f"Error managing chat: {type(e).__name__}", break_loop=False)
