## telegram_chat
Manage the Telegram chat bridge — a persistent bot that lets users chat with Agent Zero through Telegram.

**Arguments:**
- **action** (string): `start`, `stop`, `restart`, `status`, `add_chat`, `remove_chat`, or `list`
- **chat_id** (string): Chat ID (for `add_chat`/`remove_chat`)
- **label** (string): Friendly name for the chat (for `add_chat`)
- **thread_id** (string): Forum topic thread ID (optional for `add_chat` and `remove_chat`)

**start** — Launch the chat bridge bot:
~~~json
{"action": "start"}
~~~

**stop** — Stop the bot:
~~~json
{"action": "stop"}
~~~

**restart** — Restart the bot:
~~~json
{"action": "restart"}
~~~

**status** — Check bot status:
~~~json
{"action": "status"}
~~~

**add_chat** — Designate a chat for LLM bridging:
~~~json
{"action": "add_chat", "chat_id": "-1001234567890", "label": "llm-chat"}
~~~

**add_chat** — Designate a specific forum topic for LLM bridging:
~~~json
{"action": "add_chat", "chat_id": "-1001234567890", "thread_id": "42", "label": "Sprint Planning"}
~~~

**remove_chat** — Remove a chat from the bridge:
~~~json
{"action": "remove_chat", "chat_id": "-1001234567890"}
~~~

**remove_chat** — Remove a specific forum topic from the bridge:
~~~json
{"action": "remove_chat", "chat_id": "-1001234567890", "thread_id": "42"}
~~~

**list** — List all bridge chats:
~~~json
{"action": "list"}
~~~

**Notes:**
- The bot uses long polling (not webhooks) — no public URL needed
- Default mode is restricted (chat only, no tools)
- Elevated mode requires `!auth <key>` from allowed users
- Enable auto_start in config to launch on agent startup
- Use `thread_id` with `add_chat`/`remove_chat` to bridge individual forum topics in a supergroup
- Topics are listed grouped under their parent supergroup in the `list` output
