# Feature Implementation Plan

## Overview

Three feature tracks, ordered by dependency. Tracks 1 and 2 are independent of each other.
Track 3 (Supergroups) has internal sub-phases that build on each other.

---

## Track 1 — Emoji Lifecycle Reactions

### Goal
React to an incoming message with 👍 when received, 🤔 while the agent is thinking/processing,
and ✅ when the response is done. Use ❌ on error.

### How it fits the existing code
`TelegramClient.set_message_reaction()` already exists in `helpers/telegram_client.py:128`.
The `_on_message` handler in `helpers/telegram_bridge.py:359` is the single chokepoint for
all incoming messages — this is where the three reaction phases get wired in.

### Changes

#### `helpers/telegram_bridge.py`
- In `_on_message`, immediately after the message passes rate-limiting, call
  `set_message_reaction(chat_id, message.message_id, "👍")` — "received".
- Before dispatching to `_get_agent_response` / `_get_elevated_response`, swap the
  reaction to `"🤔"` — "processing".
- After `_send_response` completes successfully, swap to `"✅"` — "done".
- In the `except` block, swap to `"❌"` — "error".
- Reactions are fire-and-forget (`asyncio.create_task`); a failure must never crash the bridge.
- Only react when the message came from a designated bridge chat (after `get_chat_list` guard).

#### `helpers/telegram_client.py`
- No changes needed — `set_message_reaction` is already implemented.
- Note: Telegram only allows reactions on messages in supergroups/channels where the bot is
  an admin, or in private chats. Reaction calls in plain groups where reactions are disabled
  will raise `TelegramAPIError` — catch and log, don't propagate.

#### `default_config.yaml`
```yaml
chat_bridge:
  reactions:
    enabled: true
    on_received: "👍"
    on_thinking: "🤔"
    on_done: "✅"
    on_error: "❌"
```

#### `webui/config.html`
- Add a "Reactions" section with an enable/disable toggle and four emoji fields.

### Emoji reference (Telegram Bot API supported set)
`👍` `👎` `❤` `🔥` `🥰` `👏` `😁` `🤔` `🤯` `😱` `🤬` `😢` `🎉` `🤩` `🤮` `💩`
`🙏` `👌` `🕊` `🤡` `🥱` `🥴` `😍` `🐳` `❤‍🔥` `🌚` `🌭` `💯` `🤣` `⚡` `🍌` `🏆`
`💔` `🤨` `😐` `🍓` `🍾` `💋` `🖕` `😈` `😴` `😭` `🤓` `👻` `👨‍💻` `👀` `🎃` `🙈`
`😇` `😨` `🤝` `✍` `🤗` `🫡` `🎅` `🎄` `☃` `💅` `🤪` `🗿` `🆒` `💘` `🙉` `😎`
`👾` `🤷‍♂` `🤷` `🤷‍♀` `😡` ✅ ❌

---

## Track 2 — Streaming / Progressive Message Delivery

### Goal
Instead of sending one complete reply message, send a placeholder and progressively
edit it as content is generated — sentence by sentence, word by word, or paragraph by
paragraph. Mirrors how Claude.ai's UI streams responses.

### How it fits the existing code
Currently `_send_response` (`telegram_bridge.py:643`) sends only after the full response
is ready. The `_get_elevated_response` method (`telegram_bridge.py:536`) awaits
`context.communicate()` for the full result. Both need to be reworked.

### New API method: `edit_message`

#### `helpers/telegram_client.py`
```python
async def edit_message(
    self, chat_id: str, message_id: int, text: str,
    parse_mode: Optional[str] = None,
    message_thread_id: Optional[int] = None,
) -> dict:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return await self._post("editMessageText", payload)
```

### Streaming engine: `helpers/stream_response.py` (new file)

Responsibilities:
1. Send the initial "placeholder" message (e.g. `"…"`) and capture its `message_id`.
2. Accept text chunks from the generator.
3. Accumulate and debounce edits — minimum 1.5s between edits (Telegram rate: ~20 edits/min).
4. On the final chunk, do one last edit with the full formatted response.
5. Handle `editMessageText` failures (e.g. message too long → split and send new messages).

```python
STREAM_MODES = ("word", "sentence", "paragraph")

async def stream_response_to_telegram(
    bot,                   # python-telegram-bot Bot instance (bridge has it)
    chat_id: str,
    reply_to_message_id: int,
    token_generator,       # async generator yielding str chunks
    mode: str = "sentence",
    message_thread_id: Optional[int] = None,
) -> None: ...
```

Splitting logic per mode:
- `word` — emit after each whitespace-delimited token
- `sentence` — emit after `.`, `!`, `?`, `…` followed by a space or end
- `paragraph` — emit after `\n\n`

### Integration points

#### Restricted mode (`_get_agent_response`)
`call_utility_model()` does not currently return a streaming generator. Two options:
1. **Option A (simpler):** Keep full-response behavior for restricted mode; only stream in
   elevated mode. Configure via `stream_mode: none` for restricted, `sentence` for elevated.
2. **Option B (full):** If A0 exposes a streaming API on `call_utility_model`, wrap it.
   Investigate `agent.call_utility_model` signature before implementing.

Recommendation: **Option A** for initial implementation — deferred streaming for restricted mode
unless A0 adds a streaming utility model call.

#### Elevated mode (`_get_elevated_response`)
`context.communicate()` returns a `Task`. Check if A0's `AgentContext` exposes a streaming
version (e.g. `communicate_stream()`). If yes, wire it in. If not, fall back to the
post-processing approach: collect full response, then stream-edit it word/sentence/paragraph
by paragraph. Post-processing streaming still gives a good UX for long responses.

#### `default_config.yaml`
```yaml
chat_bridge:
  streaming:
    enabled: true
    mode: "sentence"          # word | sentence | paragraph | none
    edit_interval_ms: 1500    # minimum ms between edits (Telegram rate limit)
    placeholder: "…"          # initial message shown while generating
```

#### `webui/config.html`
- Add a "Streaming" section: enable toggle, mode selector (dropdown), edit interval slider.

---

## Track 3 — Supergroup Topics (Forum Threads)

This track has four sub-phases. Each is independently mergeable.

### Background: Telegram supergroup topics
A supergroup with "Topics" enabled exposes **Forum Topics**. Each topic is a separate
thread. In the Bot API:
- Every message in a topic carries `message_thread_id` (an int).
- The bot must be an admin (or have `can_manage_topics` permission) to create/edit topics.
- Updates include `forum_topic_created`, `forum_topic_edited`, `forum_topic_closed`,
  `forum_topic_reopened` service messages.
- Sending into a topic requires `message_thread_id` on `sendMessage`.

### OpenClaw pattern (reference)
OpenClaw uses conversation IDs of the form `{chat_id}:topic:{thread_id}` as the canonical
key for routing, session binding, and context lookup. This is the pattern we adopt.

---

### Phase 3a — Topic-aware context keying

#### Current state
Context is keyed by `chat_id` alone. `get_context_id(chat_id)` /
`set_context_id(chat_id, context_id)` in `telegram_bridge.py:83-89`.

#### Changes

**`helpers/telegram_bridge.py`**

New helper:
```python
def _topic_key(chat_id: str, thread_id: Optional[int]) -> str:
    """Returns '{chat_id}:topic:{thread_id}' for forum topics, else '{chat_id}'."""
    if thread_id:
        return f"{chat_id}:topic:{thread_id}"
    return chat_id
```

In `_on_message`:
- Extract `message_thread_id = message.message_thread_id` (may be `None` for DMs/plain groups).
- Use `_topic_key(chat_id, message_thread_id)` everywhere `chat_id` was used alone
  for context lookup and storage.

**`helpers/message_store.py`**
- Update `store_message` to accept an optional `thread_id` and include it as metadata:
  `raw_msg["message_thread_id"] = thread_id`.
- Storage key remains `chat_id` (keeps the file compact), but the thread context is
  available in the message object for routing decisions.

**`helpers/telegram_bridge.py` — `load_chat_state` / schema**
- The `contexts` dict already accepts arbitrary string keys. No schema change needed;
  `"{chat_id}:topic:{thread_id}"` keys work immediately.

---

### Phase 3b — Supergroup-aware message sending

**`helpers/telegram_client.py`**
- Update `send_message` to accept `message_thread_id: Optional[int] = None`:
  ```python
  if message_thread_id:
      payload["message_thread_id"] = message_thread_id
  ```
- Update `edit_message` (from Track 2) similarly.
- Update `set_message_reaction` similarly.

**`helpers/telegram_bridge.py`**
- Pass `message_thread_id` through `_send_response` → `_send_chunk` so all reply
  messages land in the correct topic thread.
- Store `thread_id` on `_on_message` call context and thread it through both
  `_get_agent_response` and `_get_elevated_response`.
- In `_send_chunk`: use `message.reply_text` with `message_thread_id` for first chunk,
  `message.chat.send_message` with `message_thread_id` for continuation chunks.

---

### Phase 3c — Topic-to-project mapping

#### State model

Extend `chat_bridge_state.json`:
```json
{
  "chats": { ... },
  "contexts": { ... },
  "topics": {
    "{chat_id}:topic:{thread_id}": {
      "name": "Sprint Planning",
      "project_id": "proj_abc123",
      "created_at": "2026-04-17T00:00:00Z",
      "auto_created": true
    }
  }
}
```

**`helpers/telegram_bridge.py`** — new functions:
```python
def get_topic_map() -> dict:
    return load_chat_state().get("topics", {})

def set_topic_project(topic_key: str, project_id: str, name: str, auto_created: bool = False):
    state = load_chat_state()
    state.setdefault("topics", {})[topic_key] = {
        "name": name,
        "project_id": project_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "auto_created": auto_created,
    }
    save_chat_state(state)

def get_topic_project(topic_key: str) -> Optional[dict]:
    return load_chat_state().get("topics", {}).get(topic_key)
```

#### Auto-create project on new topic

In `_run_bot_in_thread` (telegram_bridge.py), register a new handler:
```python
from telegram.ext import MessageHandler, filters
app.add_handler(MessageHandler(
    filters.StatusUpdate.FORUM_TOPIC_CREATED,
    bot._on_forum_topic_created,
))
```

`_on_forum_topic_created` handler:
1. Extract `chat_id`, `message_thread_id`, and `forum_topic_created.name`.
2. Build `topic_key = _topic_key(chat_id, message_thread_id)`.
3. If not already mapped, create a new A0 context for this topic (same pattern as
   `_get_agent_response` context creation).
4. Optionally call a project-creation hook (if project management plugin is present).
5. Store mapping via `set_topic_project(topic_key, context_id, name, auto_created=True)`.
6. React to the service message with 🎉 (optional, configurable).

#### `telegram_manage` tool — new `topic` actions
Extend `tools/telegram_manage.py` with:
- `action: "map_topic"` — manually map a `message_thread_id` to a `project_id`
- `action: "list_topics"` — list all topic→project mappings for a supergroup
- `action: "unmap_topic"` — remove a mapping

Update `prompts/agent.system.tool.telegram_manage.md` with JSON examples for topic actions.

---

### Phase 3d — Forum topic management API

**`helpers/telegram_client.py`** — new methods:
```python
async def create_forum_topic(
    self, chat_id: str, name: str,
    icon_color: Optional[int] = None,
    icon_custom_emoji_id: Optional[str] = None,
) -> dict:
    payload = {"chat_id": chat_id, "name": name}
    if icon_color:
        payload["icon_color"] = icon_color
    if icon_custom_emoji_id:
        payload["icon_custom_emoji_id"] = icon_custom_emoji_id
    return await self._post("createForumTopic", payload)
    # Returns: { message_thread_id, name, icon_color, icon_custom_emoji_id }

async def edit_forum_topic(
    self, chat_id: str, message_thread_id: int,
    name: Optional[str] = None,
    icon_custom_emoji_id: Optional[str] = None,
) -> bool:
    payload = {"chat_id": chat_id, "message_thread_id": message_thread_id}
    if name:
        payload["name"] = name
    if icon_custom_emoji_id:
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
    return await self._get("getForumTopicIconStickers")
```

**`tools/telegram_manage.py`** — add actions:
- `create_topic` — creates a forum topic, stores mapping
- `close_topic` / `reopen_topic` — topic lifecycle
- `rename_topic` — updates both Telegram and the local name in state

**`prompts/agent.system.tool.telegram_manage.md`** — add examples for all new actions.

---

## Config additions summary

Full new sections for `default_config.yaml`:

```yaml
chat_bridge:
  # --- Track 1 ---
  reactions:
    enabled: true
    on_received: "👍"
    on_thinking: "🤔"
    on_done: "✅"
    on_error: "❌"

  # --- Track 2 ---
  streaming:
    enabled: false           # off by default until stable
    mode: "sentence"         # word | sentence | paragraph | none
    edit_interval_ms: 1500
    placeholder: "…"

  # --- Track 3 ---
  supergroups:
    auto_create_topic_context: true   # auto-create A0 context on new forum topic
    auto_create_project: false        # requires project plugin integration
    topic_idle_timeout: 86400         # seconds before topic context expires (0=never)
```

---

---

## Track 1 — Implementation details (additions)

### Reaction call approach
In `_on_message`, `context_obj.bot` (python-telegram-bot `Bot`) is already in scope.
Use `context_obj.bot.set_message_reaction(chat_id, message_id, [ReactionTypeEmoji(emoji)])` 
directly — no need to create/close a `TelegramClient` for bridge-side reactions.
This is fire-and-forget: wrap each call in `asyncio.create_task(_safe_react(...))` where
`_safe_react` catches and logs all exceptions without propagating them.

Graceful degradation: `setMessageReaction` raises `BadRequest` when reactions are
disabled for the chat (old-style groups, channels without admin). Catch `BadRequest`
specifically and suppress it — log at DEBUG level, not ERROR.

Config read path: `_get_config()` in `ChatBridgeBot` returns the loaded config dict.
Reactions config lives at `config["chat_bridge"]["reactions"]`. Access via
`config.get("chat_bridge", {}).get("reactions", {})` using the same pattern already
used for `allow_elevated` and `session_timeout`.

---

## Track 2 — Implementation details (additions)

### Partial markdown / formatting strategy
`markdown_to_telegram_html` does not safely handle incomplete markdown (e.g. an
unclosed `**bold` mid-stream). Streaming intermediate edits should use **plain text**
(HTML-escaped only via `_escape_html`) with a `▌` cursor appended to indicate activity.
The **final edit only** applies full `markdown_to_telegram_html` formatting. This avoids
parse errors on partial content while still delivering the formatted final response.

Add to `helpers/format_telegram.py`:
```python
def format_streaming_chunk(text: str) -> str:
    """Escape text for a streaming-in-progress edit (no markdown conversion)."""
    return _escape_html(text) + " ▌"

def format_streaming_final(text: str) -> str:
    """Full markdown->HTML for the final streaming edit."""
    return markdown_to_telegram_html(text)
```

### Message store interaction during streaming
The current `_send_response` calls `store_message` for every sent chunk. For
streaming, the placeholder and intermediate edits must NOT generate store entries —
only the final state should be stored. Update `stream_response.py` to call
`store_message` once after the final edit resolves.

### New `edit` action on `telegram_send` tool
While adding `editMessageText` to `telegram_client.py` for streaming, also expose it
as `action: "edit"` in `tools/telegram_send.py`:
```json
{"action": "edit", "chat_id": "-100...", "message_id": "42", "content": "Updated text"}
```
Update `prompts/agent.system.tool.telegram_send.md` with the new action example.

### Track 1 + Track 2 coordination
When streaming is active, the reaction lifecycle is:
- 👍 on receive (before placeholder is sent)
- 🤔 while streaming is in progress
- ✅ after `stream_response.py` fires its final edit and completes
- ❌ if `stream_response.py` raises an unhandled exception

The reaction calls and stream edits share Telegram's rate limits. Edits count against
the 20/min per-message cap; reactions are a separate cap. Both are light enough that
they don't conflict unless the same message sees both streaming edits AND rapid
re-reactions. The debounce in `stream_response.py` (1.5s min between edits) handles this.

---

## Track 3 — Implementation details (additions)

### `tools/telegram_chat.py` — topic-aware bridge management

`add_chat` must accept an optional `thread_id` to register a specific topic as a
bridge target rather than the whole supergroup:
```json
{"action": "add_chat", "chat_id": "-1001234567890", "thread_id": "123", "label": "Sprint Planning"}
```
Internally this stores key `{chat_id}:topic:{thread_id}` in the chats dict.
`list` output should group topics under their parent supergroup.
`status` should show topic context counts alongside chat counts.
`remove_chat` must accept optional `thread_id` to remove a specific topic binding
without removing the whole supergroup.

### `tools/telegram_read.py` — topic-aware reading

New parameter `thread_id` on `messages` action filters stored messages by
`message_thread_id`:
```json
{"action": "messages", "chat_id": "-1001234567890", "thread_id": "123", "limit": "50"}
```

New `action: "topics"` — list forum topics in a supergroup (calls `getForumTopics`
Bot API method or queries the local `topics` map in state):
```json
{"action": "topics", "chat_id": "-1001234567890"}
```

`_format_chat_info` should show the `is_forum: true` flag when returned by `getChat`.
`_update_chat_registry` in `telegram_read.py` should capture `message_thread_id` from
updates so topic messages can be queried without the bridge running.

### `tools/telegram_summarize.py` — topic filtering

Add optional `thread_id` parameter: when provided, filter messages to that topic before
summarising. No structural changes to the summarise logic itself.

### `helpers/message_store.py` — key structure decision

Store topic messages under a separate key `{chat_id}:topic:{thread_id}` — consistent
with the OpenClaw pattern and with how context keys are built. This keeps topic
histories isolated (important for per-project context) without complicating the storage
format.

Changes:
- `store_message(chat_id, raw_msg)` — if `raw_msg` contains `message_thread_id`, use
  `{chat_id}:topic:{thread_id}` as the store key automatically.
- `get_messages(chat_id, thread_id=None)` — if `thread_id` is given, uses topic key;
  otherwise uses plain `chat_id`.
- `get_all_chats()` — return topic keys alongside chat keys so `telegram_read` can
  surface them.

### `helpers/sanitize.py` — composite key validation

Add `validate_topic_key(value: str)`:
```python
def validate_topic_key(value: str) -> str:
    """Validate a '{chat_id}:topic:{thread_id}' composite key."""
    parts = value.split(":topic:")
    if len(parts) == 2:
        chat_id = validate_chat_id(parts[0])
        thread_id = parts[1]
        if not re.match(r'^\d+$', thread_id):
            raise ValueError("thread_id must be a positive integer")
        return f"{chat_id}:topic:{thread_id}"
    return validate_chat_id(value)  # falls through to plain chat_id validation
```

### Prompt files — all affected files

| Prompt file | Change needed |
|-------------|--------------|
| `telegram_read.md` | Add `thread_id` param, `topics` action example |
| `telegram_send.md` | Add `message_thread_id` param, `edit` action example |
| `telegram_chat.md` | Add `thread_id` to `add_chat` / `remove_chat`, `list` showing topics |
| `telegram_manage.md` | Topic actions (already in plan) |
| `telegram_summarize.md` | Add optional `thread_id` filter example |

### `webui/main.html` — dashboard additions

The dashboard needs a **Topics panel** alongside the existing bridge status panel:
- Show each registered supergroup and its mapped topics
- "Add topic to bridge" input (chat_id + thread_id + label)
- "Remove topic" button per topic entry
- Topic→context mapping status (active/no context yet)

This requires extending `telegram_bridge_api.py` with new actions:
- `list_topics` — returns all topic entries from `chat_bridge_state.json`
- `add_topic` — calls `add_chat` logic with a topic key
- `remove_topic` — calls `remove_chat` logic with a topic key

### `webui/config.html` — supergroup config section

```html
<div class="section-title">Supergroups &amp; Topics</div>
<!-- auto_create_topic_context toggle -->
<!-- auto_create_project toggle -->
<!-- topic_idle_timeout number input -->
```

### `helpers/poll_state.py` — topic-aware watching

`add_watch_chat` / `remove_watch_chat` should accept an optional `thread_id` so
background polling can watch specific topics. Key stored as
`{chat_id}:topic:{thread_id}` when thread_id is present.

### Skill files — trigger pattern updates

All three skills need updated capability descriptions:

| Skill | Addition |
|-------|---------|
| `skills/telegram-research/SKILL.md` | "read messages from a supergroup topic" trigger |
| `skills/telegram-communicate/SKILL.md` | "send to a topic thread", "create a forum topic" trigger |
| `skills/telegram-chat/SKILL.md` | "bridge a supergroup topic", "map topic to project" trigger |

### Backward compatibility — `chat_bridge_state.json`

Existing state files without a `topics` key load cleanly because all new functions use
`state.get("topics", {})` / `state.setdefault("topics", {})`. No migration script needed.
Add a note to `RELEASE.md` that existing state files are forward-compatible.

---

## Shared infrastructure — `helpers/rate_limiter.py` (new file)

Both Track 1 (reaction updates) and Track 2 (stream edits) add Telegram API calls in
the hot message-handling path. A token bucket rate limiter prevents accidental burst
violations in high-volume chats.

```python
class TelegramRateLimiter:
    """Token bucket rate limiter for Telegram Bot API calls.
    
    Telegram limits: 30 msg/s global, 1 msg/s per chat, 20 edits/min per message.
    """
    def __init__(self, rate: float, per: float):
        self.rate = rate          # tokens per period
        self.per = per            # period in seconds
        self._tokens: dict[str, float] = {}
        self._last: dict[str, float] = {}

    async def acquire(self, key: str = "global") -> None:
        """Wait until a token is available for the given key."""
        ...
```

Used by:
- `stream_response.py` — per-message edit limiter (20/min = 1 per 3s, but 1.5s debounce is safe)
- `telegram_bridge.py` — shared limiter instance on `ChatBridgeBot` for reactions

---

## Files touched per track (complete)

| File | Track 1 | Track 2 | Track 3 |
|------|---------|---------|---------|
| `helpers/telegram_bridge.py` | ✅ reactions hook | ✅ streaming dispatch | ✅ topic keying, handlers |
| `helpers/telegram_client.py` | — | ✅ `edit_message` | ✅ `message_thread_id`, forum methods |
| `helpers/stream_response.py` | — | ✅ new file | — |
| `helpers/rate_limiter.py` | ✅ reaction limiter | ✅ edit limiter | — |
| `helpers/message_store.py` | — | ✅ final-only storage | ✅ topic key, thread filtering |
| `helpers/format_telegram.py` | — | ✅ streaming chunk helpers | — |
| `helpers/sanitize.py` | — | — | ✅ `validate_topic_key` |
| `helpers/poll_state.py` | — | — | ✅ topic-aware watch keys |
| `tools/telegram_manage.py` | — | — | ✅ topic CRUD actions |
| `tools/telegram_send.py` | — | ✅ `edit` action | ✅ `message_thread_id` param |
| `tools/telegram_read.py` | — | — | ✅ `thread_id` filter, `topics` action |
| `tools/telegram_chat.py` | — | — | ✅ topic-aware add/remove/list |
| `tools/telegram_summarize.py` | — | — | ✅ `thread_id` filter |
| `prompts/agent.system.tool.telegram_manage.md` | — | — | ✅ topic examples |
| `prompts/agent.system.tool.telegram_send.md` | — | ✅ `edit` example | ✅ `message_thread_id` example |
| `prompts/agent.system.tool.telegram_read.md` | — | — | ✅ `thread_id`, `topics` examples |
| `prompts/agent.system.tool.telegram_chat.md` | — | — | ✅ topic add/remove examples |
| `prompts/agent.system.tool.telegram_summarize.md` | — | — | ✅ `thread_id` example |
| `skills/telegram-research/SKILL.md` | — | — | ✅ trigger update |
| `skills/telegram-communicate/SKILL.md` | — | — | ✅ trigger update |
| `skills/telegram-chat/SKILL.md` | — | — | ✅ trigger update |
| `api/telegram_bridge_api.py` | — | — | ✅ topic list/add/remove actions |
| `default_config.yaml` | ✅ reactions | ✅ streaming | ✅ supergroups |
| `webui/config.html` | ✅ reactions UI | ✅ streaming UI | ✅ supergroup config UI |
| `webui/main.html` | — | — | ✅ topics dashboard panel |
| `plugin.yaml` | — | — | ✅ version → 1.2.0 |
| `RELEASE.md` | ✅ | ✅ | ✅ |
| `tests/regression_test.sh` | ✅ | ✅ | ✅ |
| `tests/HUMAN_TEST_PLAN.md` | ✅ | ✅ | ✅ |

---

## Implementation order recommendation

1. **Track 1** — smallest change, highest user visibility, validates the
   `set_message_reaction` path end-to-end. Also validates `_get_config()` reads in bridge.
2. **`helpers/rate_limiter.py`** — build before Track 2 since both tracks use it.
3. **Track 2** — self-contained streaming engine gated behind `streaming.enabled: false`.
   Requires `format_telegram.py` chunk helpers first, then `stream_response.py`, then
   bridge integration.
4. **Track 3a + 3b** — topic keying + thread-aware sending (must be done together).
   Requires `sanitize.py` `validate_topic_key` and `message_store.py` key changes first.
5. **Track 3c** — topic-to-project mapping + `telegram_chat.py` / `telegram_read.py`
   tool updates + dashboard panel. Largest surface area.
6. **Track 3d** — forum management API (purely additive, lowest urgency).

---

## Open questions before implementation

1. Does `AgentContext.communicate()` expose a streaming/generator API in the current
   A0 version? If yes, Track 2 elevated mode streams natively. If no, use
   post-processing stream (send placeholder, await full result, stream-edit the result
   word/sentence/paragraph). Check A0's `agent.py` before starting Track 2.
2. Does A0's project plugin expose a `create_project(name)` call that Track 3c can
   hook into for auto-project-on-topic-create? Or should `auto_create_project` remain
   a stub (config flag exists, functionality deferred) for the initial release?
3. What `getForumTopics` Bot API method returns — verify this endpoint exists and
   returns topic metadata. Alternative: query the local state for known topics
   (populated by `FORUM_TOPIC_CREATED` updates) rather than hitting the API.

---
---
---

# Additional Features — Tracks 4–11

These tracks are independent of each other and of Tracks 1–3.
They are ordered by implementation priority (highest value / lowest complexity first).

---

## Track 4 — Inline Keyboard Buttons + Callback Queries

### Goal
Allow the bot to send messages with tappable inline buttons. Primary use case: a
**tool-approval workflow** in elevated mode — before executing a destructive or
significant action, the agent sends a Telegram message listing what it's about to do
with `[Approve]` and `[Reject]` buttons. Secondary uses: quick-reply shortcuts,
confirmation dialogs, topic-creation confirmations (Track 3).

This mirrors OpenClaw's `interactive-dispatch.ts` / `exec-approval-forwarding.ts`.

### Telegram Bot API concepts
- `sendMessage` with `reply_markup: { inline_keyboard: [[{text, callback_data}]] }`
  attaches buttons to a message.
- When a user taps a button, Telegram sends a `callback_query` update containing
  `data` (the `callback_data` string) and `message` (the original message).
- The bot must call `answerCallbackQuery(callback_query_id)` within 10 seconds or
  Telegram shows a loading spinner forever.
- After answering, the bot can optionally edit the original message to remove or
  replace the buttons via `editMessageReplyMarkup`.

### New API methods — `helpers/telegram_client.py`

```python
async def send_message_with_buttons(
    self,
    chat_id: str,
    text: str,
    buttons: list[list[dict]],          # [[{text, callback_data}], ...]
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
    """Acknowledge a callback query (required within 10 seconds of receipt)."""
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
    buttons: Optional[list[list[dict]]] = None,  # None = remove keyboard
) -> dict:
    """Replace or remove the inline keyboard on an existing message."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": buttons or []},
    }
    return await self._post("editMessageReplyMarkup", payload)
```

### New helper — `helpers/button_builder.py` (new file)

Centralises button construction so tools and bridge code don't build raw dicts.

```python
def approval_buttons(approve_data: str = "approve", reject_data: str = "reject"):
    """Standard two-button approval row."""
    return [[
        {"text": "✅ Approve", "callback_data": approve_data},
        {"text": "❌ Reject",  "callback_data": reject_data},
    ]]

def choice_buttons(choices: list[str], prefix: str = "choice") -> list[list[dict]]:
    """One button per choice, each on its own row."""
    return [[{"text": c, "callback_data": f"{prefix}:{c}"}] for c in choices]

def confirm_button(text: str = "Confirm", data: str = "confirm"):
    return [[{"text": text, "callback_data": data}]]
```

### Callback query handler — `helpers/telegram_bridge.py`

#### Registration in `_run_bot_in_thread`
```python
from telegram.ext import CallbackQueryHandler
app.add_handler(CallbackQueryHandler(bot._on_callback_query))
```

Also add `"callback_query"` to `allowed_updates` when starting the updater:
```python
await app.updater.start_polling(
    drop_pending_updates=True,
    allowed_updates=["message", "callback_query", "message_reaction",
                     "forum_topic_created", "edited_message"],
)
```

#### `_on_callback_query` method on `ChatBridgeBot`

```python
async def _on_callback_query(self, update, context_obj):
    """Handle inline keyboard button taps."""
    query = update.callback_query
    if not query:
        return

    user_id = str(query.from_user.id)
    chat_id = str(query.message.chat_id)
    data = query.data or ""

    # Always answer to clear the loading spinner
    await query.answer()

    # Look up a pending approval for this (chat_id, message_id)
    message_id = query.message.message_id
    approval_key = f"{chat_id}:{message_id}"

    pending = self._pending_approvals.pop(approval_key, None)
    if pending is None:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # Resolve: set the future result so the waiting coroutine unblocks
    future: asyncio.Future = pending["future"]
    if not future.done():
        future.set_result(data)

    # Edit the approval message to show outcome
    outcome_text = pending["message_text"]
    if data == "approve" or data.startswith("approve:"):
        outcome_text += "\n\n✅ <b>Approved</b>"
    else:
        outcome_text += "\n\n❌ <b>Rejected</b>"

    try:
        await query.edit_message_text(outcome_text, parse_mode="HTML")
    except Exception:
        pass
```

#### Pending approval registry on `ChatBridgeBot.__init__`
```python
# Maps "{chat_id}:{message_id}" -> {"future": asyncio.Future, "message_text": str}
self._pending_approvals: dict[str, dict] = {}
```

#### Public method for elevated-mode tool approval
```python
async def request_approval(
    self,
    chat_id: str,
    action_description: str,
    timeout: float = 120.0,
    thread_id: Optional[int] = None,
) -> bool:
    """
    Send an approval request to Telegram and wait for user response.
    Returns True if approved, False if rejected or timed out.
    
    Called by elevated-mode agent loop hooks when a tool requires approval.
    """
    from usr.plugins.telegram.helpers.button_builder import approval_buttons
    from usr.plugins.telegram.helpers.format_telegram import markdown_to_telegram_html

    text = (
        "🔐 <b>Action Approval Required</b>\n\n"
        f"{markdown_to_telegram_html(action_description)}\n\n"
        "<i>This action will be taken by Agent Zero in elevated mode.</i>"
    )
    buttons = approval_buttons()
    client = TelegramClient(self.bot_token)
    try:
        sent = await client.send_message_with_buttons(
            chat_id, text, buttons,
            parse_mode="HTML", message_thread_id=thread_id,
        )
    finally:
        await client.close()

    message_id = sent["message_id"]
    approval_key = f"{chat_id}:{message_id}"
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    self._pending_approvals[approval_key] = {
        "future": future,
        "message_text": text,
    }

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result == "approve" or result.startswith("approve:")
    except asyncio.TimeoutError:
        self._pending_approvals.pop(approval_key, None)
        return False
```

### New `telegram_send` tool action — `send_buttons`

```json
{
  "action": "send_buttons",
  "chat_id": "-1001234567890",
  "content": "Please choose an option:",
  "buttons": [["Option A", "option_a"], ["Option B", "option_b"]]
}
```

The `buttons` array is `[[label, callback_data], ...]`. Each inner list is one row.

### `default_config.yaml` addition
```yaml
chat_bridge:
  approvals:
    enabled: false              # require Telegram approval for elevated tool use
    timeout: 120                # seconds to wait for approval before auto-reject
    require_for: []             # tool names requiring approval, empty = all tools
```

### `webui/config.html` addition
```html
<div class="section-title">Tool Approvals</div>
<div class="field">
  <div class="field-label">
    <div class="field-title">Require Approval</div>
    <div class="field-description">Send Telegram approval requests before elevated tool use.</div>
  </div>
  <div class="field-control">
    <input type="checkbox" x-model="config.chat_bridge.approvals.enabled" />
  </div>
</div>
<div class="field">
  <div class="field-label">
    <div class="field-title">Approval Timeout (seconds)</div>
  </div>
  <div class="field-control">
    <input type="number" x-model="config.chat_bridge.approvals.timeout" min="30" max="600" />
  </div>
</div>
```

### Security note
`callback_data` is set by the bot; it is not user-controlled input. However,
`answerCallbackQuery` must always be called to prevent UI hang — do this before any
logic so a crash after the answer doesn't leave the user stuck.
`query.from_user.id` should be validated against `allowed_users` before acting.

---

## Track 5 — Edited Message Handling

### Goal
When a user edits a Telegram message that the bridge has already responded to,
re-process the edited text and replace or append the new response. Without this,
common typo-fix edits get silently ignored and the user gets an answer to the wrong question.

### Telegram Bot API behaviour
Telegram sends an `edited_message` update (same structure as `message`) when a user
edits within the edit window (~48 hours for regular messages). The update contains the
new text and the original `message_id`.

### Changes

#### `_run_bot_in_thread` — `helpers/telegram_bridge.py`
Register an edited-message handler:
```python
app.add_handler(MessageHandler(
    filters.UpdateType.EDITED_MESSAGE & filters.TEXT,
    bot._on_edited_message,
))
```

#### `_on_edited_message` on `ChatBridgeBot`
```python
async def _on_edited_message(self, update, context_obj):
    """Re-process an edited user message."""
    # Edited message arrives in update.edited_message, not update.message
    message = update.edited_message
    if not message or not message.text:
        return

    # Swap update.message reference so _on_message sees the edited message
    update._unfreeze()
    update.message = message
    update._freeze()

    # Only re-process if the original message is recent (within 10 minutes)
    # to avoid reprocessing old edits on bot restart
    import time
    if time.time() - message.date.timestamp() > 600:
        return

    # Delete the original bot response if we have it tracked
    chat_id = str(message.chat_id)
    orig_id = message.message_id
    tracked_reply = self._sent_replies.pop(f"{chat_id}:{orig_id}", None)
    if tracked_reply:
        try:
            await context_obj.bot.delete_message(chat_id, tracked_reply)
        except Exception:
            pass

    await self._on_message(update, context_obj)
```

#### Sent-reply tracking on `ChatBridgeBot.__init__`
```python
# Maps "{chat_id}:{user_message_id}" -> bot_reply_message_id
self._sent_replies: dict[str, int] = {}
```

In `_send_response`, after the first chunk is sent, store:
```python
self._sent_replies[f"{chat_id}:{original_message_id}"] = sent.message_id
```

Cap the dict at 500 entries (rotate oldest) to prevent unbounded growth.

#### `default_config.yaml` addition
```yaml
chat_bridge:
  handle_edited_messages: true
  edited_message_window: 600    # seconds; ignore edits older than this
```

---

## Track 6 — Bot Commands via `setMyCommands`

### Goal
Replace the non-standard `!auth` / `!deauth` / `!status` prefix system with Telegram's
native `/command` interface. Registered commands appear in the Telegram client's
command hint menu when users type `/`. This is the expected UX for Telegram bots.

### New commands to register

| Command | Replaces | Description |
|---------|----------|-------------|
| `/auth <key>` | `!auth <key>` | Elevate to full agent access |
| `/deauth` | `!deauth` | End elevated session |
| `/status` | `!bridge-status` | Show current session mode |
| `/help` | — | List available commands |
| `/newcontext` | — | Clear conversation history and start fresh |
| `/cancel` | — | Cancel the current in-progress agent action |

Keep `!auth` / `!deauth` working as aliases for backward compatibility (one sprint, then deprecate).

### New API methods — `helpers/telegram_client.py`
```python
async def set_my_commands(
    self,
    commands: list[dict],               # [{"command": "auth", "description": "..."}]
    scope: Optional[dict] = None,       # BotCommandScope (None = default scope)
    language_code: Optional[str] = None,
) -> bool:
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
```

### Registration in `_run_bot_in_thread` — `helpers/telegram_bridge.py`

After `await app.bot.get_me()` succeeds, register commands:
```python
BRIDGE_COMMANDS = [
    {"command": "auth",       "description": "Elevate to full Agent Zero access (!auth <key>)"},
    {"command": "deauth",     "description": "End elevated session"},
    {"command": "status",     "description": "Show current session mode and expiry"},
    {"command": "help",       "description": "List available commands"},
    {"command": "newcontext", "description": "Clear conversation history and start fresh"},
    {"command": "cancel",     "description": "Cancel the current in-progress task"},
]
try:
    await app.bot.set_my_commands(BRIDGE_COMMANDS)
    logger.info("Registered bot commands")
except Exception as e:
    logger.warning(f"Could not register bot commands: {e}")
```

### Updated command handler — `helpers/telegram_bridge.py`

Update `_handle_auth_command` to also handle `/auth`, `/deauth`, `/status` prefixes.
Register a separate `CommandHandler` for `/help`, `/newcontext`, `/cancel`:
```python
from telegram.ext import CommandHandler
app.add_handler(CommandHandler("auth",       bot._cmd_auth))
app.add_handler(CommandHandler("deauth",     bot._cmd_deauth))
app.add_handler(CommandHandler("status",     bot._cmd_status))
app.add_handler(CommandHandler("help",       bot._cmd_help))
app.add_handler(CommandHandler("newcontext", bot._cmd_newcontext))
app.add_handler(CommandHandler("cancel",     bot._cmd_cancel))
```

#### `/help` response
```
Available commands:
/auth <key> — Elevate to full Agent Zero access
/deauth — End elevated session
/status — Show current mode and session expiry
/newcontext — Clear conversation history
/cancel — Cancel current in-progress task

Current mode: Restricted (chat only)
```

#### `/newcontext` implementation
Clears `self._conversations[chat_id]` and calls `set_context_id(chat_id, "")` so the
next message creates a fresh A0 context. Sends confirmation: "Conversation reset. Starting fresh."

#### `/cancel` implementation
Sets a cancellation flag `self._cancel_requested: dict[str, bool]` keyed by chat_id.
Check this flag at each `asyncio.sleep` point in streaming (Track 2) and in the
elevated response coroutine. On cancel: send "⚠️ Task cancelled." and clear the flag.

---

## Track 7 — Webhook Mode

### Goal
Production-grade alternative to polling. Webhooks push updates to the bot immediately
rather than requiring long-polling requests. Eliminates the `getUpdates` single-consumer
constraint (no more `is_bridge_polling()` guard needed in tools). Requires the server to
be publicly reachable via HTTPS.

### How webhooks work
1. Call `setWebhook(url, secret_token)` — registers the bot's endpoint with Telegram.
2. Telegram POSTs every update to `https://your-server.com/webhook/<token>` as JSON.
3. The endpoint must respond `200 OK` within 60 seconds.
4. Call `deleteWebhook()` to revert to polling.

### New API methods — `helpers/telegram_client.py`
```python
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
    return await self._post("deleteWebhook",
                            {"drop_pending_updates": drop_pending_updates})

async def get_webhook_info(self) -> dict:
    return await self._get("getWebhookInfo")
```

### New API endpoint — `api/telegram_webhook_api.py` (new file)

Receives Telegram webhook POSTs and feeds them into the running bridge:
```python
class TelegramWebhookApi(ApiHandler):
    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return True  # Use secret_token header instead of CSRF for Telegram

    async def process(self, input: dict, request: Request) -> dict:
        # Verify X-Telegram-Bot-Api-Secret-Token header
        config = get_telegram_config()
        expected_secret = config.get("chat_bridge", {}).get("webhook_secret", "")
        provided_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if expected_secret and not hmac.compare_digest(provided_secret, expected_secret):
            return {"error": "Unauthorized"}

        # Feed the raw update into the bridge's application dispatcher
        from usr.plugins.telegram.helpers.telegram_bridge import get_bridge_instance
        bridge = get_bridge_instance()
        if bridge and bridge._application:
            update = Update.de_json(input, bridge._application.bot)
            await bridge._application.process_update(update)

        return {"ok": True}
```

### Bridge mode switching — `helpers/telegram_bridge.py`

In `start_chat_bridge`, check config for mode:
```python
mode = config.get("chat_bridge", {}).get("mode", "polling")
if mode == "webhook":
    await _start_webhook_mode(bot, config)
else:
    await _start_polling_mode(bot, ready_event)
```

### `default_config.yaml` addition
```yaml
chat_bridge:
  mode: "polling"               # polling | webhook
  webhook_url: ""               # e.g. https://my-server.com/api/plugins/telegram/webhook
  webhook_secret: ""            # auto-generated if empty when mode=webhook
  webhook_max_connections: 40   # Telegram concurrent connections (1-100)
```

### `webui/config.html` addition
```html
<div class="section-title">Connection Mode</div>
<div class="field">
  <div class="field-label">
    <div class="field-title">Bridge Mode</div>
    <div class="field-description">Polling works everywhere. Webhook requires a public HTTPS URL.</div>
  </div>
  <div class="field-control">
    <select x-model="config.chat_bridge.mode">
      <option value="polling">Polling (recommended)</option>
      <option value="webhook">Webhook (production)</option>
    </select>
  </div>
</div>
<template x-if="config.chat_bridge.mode === 'webhook'">
  <div>
    <!-- webhook_url, webhook_secret fields -->
  </div>
</template>
```

### `webui/main.html` addition
Show webhook status in the bridge status panel:
- Mode: Polling / Webhook
- If webhook: last ping, pending updates, URL configured

---

## Track 8 — Voice Message Transcription

### Goal
Telegram users send voice notes frequently. Currently `_on_message` ignores
`message.voice` and `message.audio`. Transcribe audio to text before feeding it to
the agent — so voice feels like a first-class input method.

### Implementation approach
1. On `message.voice` (OGG/Opus format), download the file.
2. Pass to A0's built-in transcription tool (or `call_utility_model` with audio
   attachment if A0 supports it). Fallback: shell out to `whisper` CLI if installed.
3. Prepend `[Voice message transcription]: ` to the text, then process as a normal
   message through the existing bridge pipeline (reactions, streaming, etc.).

### Changes — `helpers/telegram_bridge.py`

Update `_on_message` filter:
```python
# Before: only handles text
if not message or not message.text:
    return

# After: also handle voice/audio
if not message:
    return
if not message.text and not message.voice and not message.audio:
    return
```

New method `_transcribe_voice`:
```python
async def _transcribe_voice(self, message) -> Optional[str]:
    """Download and transcribe a voice message. Returns transcript or None."""
    try:
        import tempfile
        voice = message.voice or message.audio
        file = await voice.get_file()
        suffix = ".ogg" if message.voice else ".mp3"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            await file.download_to_drive(tmp.name)
            tmp_path = tmp.name
        self._temp_files.append(tmp_path)

        # Try A0's call_utility_model with audio attachment
        from agent import AgentContext, AgentContextType
        from initialize import initialize_agent
        config = initialize_agent()
        context = AgentContext(config=config, type=AgentContextType.USER)
        transcript = await context.agent0.call_utility_model(
            system="Transcribe the following audio file. Return only the transcript text.",
            message="[Audio file attached]",
            attachments=[tmp_path],
        )
        return transcript.strip() if transcript else None
    except Exception as e:
        logger.warning(f"Voice transcription failed: {e}")
        return None
```

In `_on_message`, before routing:
```python
if message.voice or message.audio:
    transcript = await self._transcribe_voice(message)
    if transcript:
        # Create a synthetic text message for the rest of the pipeline
        user_text = f"[Voice message]: {transcript}"
    else:
        await message.reply_text("⚠️ Could not transcribe voice message.")
        return
else:
    user_text = message.text
```

### `default_config.yaml` addition
```yaml
chat_bridge:
  voice:
    enabled: true
    transcribe_audio: true        # also transcribe audio files (not just voice notes)
    max_duration_seconds: 300     # ignore voice messages longer than 5 minutes
```

---

## Track 9 — Document / File Handling in Elevated Mode

### Goal
In elevated mode, when a user sends a document (PDF, code file, CSV, image), download
it and pass it as an attachment to `context.communicate()` — same pattern as photos
(already implemented at `telegram_bridge.py:563`). Enables the agent to read files,
analyze documents, and answer questions about attachments.

### Supported Telegram update types
- `message.document` — any file (PDF, .py, .csv, .txt, .zip, etc.)
- `message.photo` — already handled, no change
- `message.video_note` — short round video
- `message.sticker` with `is_animated=False` — static sticker (PNG)

### Changes — `helpers/telegram_bridge.py`

Extend the attachment-handling block in `_get_elevated_response`:
```python
# Existing photo handling:
if message.photo:
    photo = message.photo[-1]
    file = await photo.get_file()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    await file.download_to_drive(tmp.name)
    tmp.close()
    attachment_paths.append(tmp.name)
    self._temp_files.append(tmp.name)

# New document handling:
if message.document:
    doc = message.document
    # Enforce size limit to prevent OOM (configurable, default 20MB)
    max_bytes = config.get("chat_bridge", {}).get("max_attachment_bytes", 20_971_520)
    if doc.file_size and doc.file_size > max_bytes:
        await message.reply_text(
            f"⚠️ File too large ({doc.file_size // 1_048_576}MB). "
            f"Max: {max_bytes // 1_048_576}MB."
        )
    else:
        from usr.plugins.telegram.helpers.sanitize import sanitize_filename
        safe_name = sanitize_filename(doc.file_name or "document")
        suffix = Path(safe_name).suffix or ".bin"
        file = await doc.get_file()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        await file.download_to_drive(tmp.name)
        tmp.close()
        attachment_paths.append(tmp.name)
        self._temp_files.append(tmp.name)
        if not safe_text:
            safe_text = f"[Attached file: {safe_name}]"
```

### `telegram_read.py` — document message display

`format_messages` in `telegram_client.py` already shows `[Document: filename]`.
No changes required.

### `default_config.yaml` addition
```yaml
chat_bridge:
  attachments:
    max_size_mb: 20             # max file size for document downloads
    allowed_extensions: []      # empty = all; or [".pdf", ".py", ".csv"]
    download_in_restricted: false  # allow document downloads in restricted mode
```

---

## Track 10 — Conversation Memory Persistence

### Goal
`self._conversations` in `ChatBridgeBot` is an in-memory `dict` that is lost on every
bridge restart. Users who rely on the chat bridge see a cold-start context after any
restart. Persist the conversation history to disk.

### Changes — `helpers/conversation_store.py` (new file)

```python
"""Persistent conversation history for the chat bridge.

Separate from message_store.py (which stores raw Telegram messages).
This stores the summarised conversation history passed to the LLM.
"""
import json, os, time
from pathlib import Path

MAX_HISTORY_PER_CHAT = 20    # mirror ChatBridgeBot.MAX_HISTORY_MESSAGES

def _store_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / "conversation_history.json",
        Path("/a0/usr/plugins/telegram/data/conversation_history.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    path = candidates[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def load_history(chat_key: str) -> list[dict]:
    """Load conversation history for a chat or topic key."""
    try:
        path = _store_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        return data.get(chat_key, [])
    except Exception:
        return []

def save_history(chat_key: str, history: list[dict]):
    """Persist (trimmed) conversation history for a chat or topic key."""
    from usr.plugins.telegram.helpers.sanitize import secure_write_json
    try:
        path = _store_path()
        data = {}
        if path.exists():
            data = json.loads(path.read_text())
        data[chat_key] = history[-MAX_HISTORY_PER_CHAT:]
        secure_write_json(path, data)
    except Exception:
        pass  # Never raise — history loss is preferable to a bridge crash

def clear_history(chat_key: str):
    """Clear persisted history for a chat (used by /newcontext)."""
    save_history(chat_key, [])
```

### Changes — `helpers/telegram_bridge.py`

Replace in-memory `_conversations` access:

```python
# Existing (in _get_agent_response):
if chat_id not in self._conversations:
    self._conversations[chat_id] = []
history = self._conversations[chat_id]

# New:
from usr.plugins.telegram.helpers.conversation_store import load_history, save_history
history = load_history(chat_key)   # chat_key = _topic_key(chat_id, thread_id)
```

After appending the assistant response:
```python
save_history(chat_key, history)
```

The in-memory `_conversations` dict becomes a write-through cache:
load from disk if not in memory, write to disk after every update.

### `/newcontext` command (Track 6) integration

```python
async def _cmd_newcontext(self, update, context_obj):
    chat_id = str(update.message.chat_id)
    thread_id = getattr(update.message, "message_thread_id", None)
    key = _topic_key(chat_id, thread_id)
    self._conversations.pop(key, None)
    from usr.plugins.telegram.helpers.conversation_store import clear_history
    clear_history(key)
    set_context_id(key, "")
    await update.message.reply_text("🔄 Conversation reset. Starting fresh.")
```

---

## Track 11 — Bridge Health Monitor + Auto-Restart

### Goal
If the bridge polling thread dies (network outage, token revocation, Telegram API error),
it currently stays dead until a human manually restarts it via the WebUI or tool.
A watchdog task should detect failure and restart with exponential backoff.

### Changes — `extensions/python/agent_init/_10_telegram_chat.py`

Add a watchdog coroutine that starts alongside the bridge:
```python
async def _watchdog(bot_token: str, check_interval: int = 60):
    """Restart the bridge if it dies. Exponential backoff on repeated failures."""
    import asyncio
    from usr.plugins.telegram.helpers import telegram_bridge as bridge
    
    consecutive_failures = 0
    while True:
        await asyncio.sleep(check_interval)
        if bridge.is_bridge_polling():
            consecutive_failures = 0
            continue
        
        # Bridge is dead — attempt restart
        backoff = min(30 * (2 ** consecutive_failures), 3600)
        logger.warning(f"Bridge watchdog: bridge is dead. Restarting in {backoff}s...")
        await asyncio.sleep(backoff)
        
        try:
            await bridge.start_chat_bridge(bot_token)
            consecutive_failures = 0
            logger.warning("Bridge watchdog: restart succeeded.")
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Bridge watchdog: restart failed ({type(e).__name__}). "
                         f"Attempt {consecutive_failures}, next backoff: "
                         f"{min(30 * (2 ** consecutive_failures), 3600)}s")
```

Schedule watchdog alongside bridge start:
```python
loop.create_task(bridge.start_chat_bridge(bot_token))
loop.create_task(_watchdog(bot_token))
```

### `webui/main.html` addition
Show watchdog status and restart count in the bridge panel:
- "Auto-restart: enabled / disabled"
- "Restarts since boot: N"

### `default_config.yaml` addition
```yaml
chat_bridge:
  watchdog:
    enabled: true
    check_interval: 60          # seconds between health checks
    max_restarts_per_hour: 10   # circuit breaker; 0 = unlimited
```

---

## Track 12 — Concurrent Message Handling

### Goal
The bridge currently handles messages sequentially: if two users send messages
simultaneously, the second user waits until the first user's full agent response is
returned. A semaphore allows N concurrent responses so all users get their 👍 reaction
immediately and responses arrive as they're ready.

### Changes — `helpers/telegram_bridge.py`

Add to `ChatBridgeBot.__init__`:
```python
config = self._get_config()
max_concurrent = config.get("chat_bridge", {}).get("max_concurrent", 3)
self._concurrency_sem = asyncio.Semaphore(max_concurrent)
```

Wrap the agent call in `_on_message`:
```python
async with self._concurrency_sem:
    try:
        if is_elevated:
            response_text = await self._get_elevated_response(chat_id, user_text, message)
        else:
            response_text = await self._get_agent_response(chat_id, user_text, message)
    except Exception as e:
        ...
```

Note: The 👍 / 🤔 reactions (Track 1) happen BEFORE acquiring the semaphore (so users
see immediate acknowledgment even when the queue is full). The ✅ / ❌ happen AFTER.

### `default_config.yaml` addition
```yaml
chat_bridge:
  max_concurrent: 3             # simultaneous agent responses (1-10)
```

---

## Track 13 — Polls via `sendPoll`

### Goal
Allow the agent to create a Telegram poll (single or multiple choice) to collect
structured input from users. Useful for project planning, feature voting, preference
selection, approval workflows requiring more than yes/no.

### New API method — `helpers/telegram_client.py`
```python
async def send_poll(
    self,
    chat_id: str,
    question: str,
    options: list[str],
    is_anonymous: bool = True,
    allows_multiple_answers: bool = False,
    message_thread_id: Optional[int] = None,
) -> dict:
    payload = {
        "chat_id": chat_id,
        "question": question[:300],                      # Telegram limit
        "options": [{"text": o[:100]} for o in options],  # Telegram limit
        "is_anonymous": is_anonymous,
        "allows_multiple_answers": allows_multiple_answers,
    }
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    return await self._post("sendPoll", payload)

async def stop_poll(self, chat_id: str, message_id: int) -> dict:
    """Close a poll so no more votes can be submitted."""
    return await self._post("stopPoll", {
        "chat_id": chat_id,
        "message_id": message_id,
    })
```

### New `telegram_send` tool action
```json
{
  "action": "poll",
  "chat_id": "-1001234567890",
  "content": "Which feature should we build next?",
  "options": ["Streaming responses", "Voice support", "Document handling"],
  "allows_multiple_answers": false
}
```

Add `action: "stop_poll"` as well:
```json
{"action": "stop_poll", "chat_id": "-1001234567890", "message_id": "99"}
```

### Prompt update — `prompts/agent.system.tool.telegram_send.md`
Add poll action examples and note the 300-char question / 10-option / 100-char-per-option
limits.

---

## Track 14 — Group Privacy Mode Detection

### Goal
A common setup mistake: if Privacy Mode is ON in BotFather, the bot only receives
messages that start with `/` in groups. The bridge silently receives nothing. The
`telegram_test` connection checker should detect and warn about this.

### Changes — `api/telegram_test.py`

After `getMe` succeeds, call `getChat` on the bot's own user ID and check capabilities.
Also call `getMyDefaultAdministratorRights` — if it indicates limited read access,
warn the user:

```python
async def _check_privacy_mode(client: TelegramClient) -> dict:
    """Check if bot privacy mode may be blocking group messages."""
    try:
        me = await client.get_me()
        # Telegram does not expose privacy mode directly via API.
        # We surface it as an advisory: instruct users to check in BotFather.
        return {
            "can_read_all_group_messages": me.get("can_read_all_group_messages", False),
            "advisory": (
                "If the bot is not receiving group messages, disable Privacy Mode "
                "in BotFather → Bot Settings → Group Privacy → Turn off."
                if not me.get("can_read_all_group_messages", False)
                else None
            ),
        }
    except Exception:
        return {}
```

Expose this in the `telegram_test` response JSON so the WebUI can display a warning.

### `webui/main.html` addition
Show a yellow advisory banner when `can_read_all_group_messages` is false:
```html
<template x-if="status.privacy_advisory">
  <div class="advisory">⚠️ Privacy Mode may be ON. Bot may not receive group messages.
    <a href="https://core.telegram.org/bots/features#privacy-mode" target="_blank">Learn more</a>
  </div>
</template>
```

---

## Track 15 — Sticker Sending + Message Deletion

### Goal
Two small tool actions that round out the `telegram_send` capability surface.

**Sticker**: The agent can send a sticker as an expressive, lightweight reaction or sign-off.
Useful for personality and low-latency acknowledgments.

**Delete**: The agent can delete its own messages. Used to clean up placeholder messages,
remove errors, or retract incorrect information. (The bridge already calls
`message.delete()` on `!auth` messages — just expose it as a tool action.)

### New API methods — `helpers/telegram_client.py`
```python
async def send_sticker(
    self,
    chat_id: str,
    sticker: str,                 # file_id, file_url, or "random" for a preset
    message_thread_id: Optional[int] = None,
) -> dict:
    payload = {"chat_id": chat_id, "sticker": sticker}
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    return await self._post("sendSticker", payload)

async def delete_message(self, chat_id: str, message_id: int) -> bool:
    return await self._post("deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
    })

async def get_sticker_set(self, name: str) -> dict:
    return await self._post("getStickerSet", {"name": name})
```

### New `telegram_send` tool actions
```json
{"action": "sticker", "chat_id": "-100...", "sticker": "CAACAgI..."}
{"action": "delete",  "chat_id": "-100...", "message_id": "42"}
```

Add examples to `prompts/agent.system.tool.telegram_send.md`.

---

## Track 16 — Per-User Context in Group Chats

### Goal
In a group chat, all users currently share a single A0 context — Alice's question and
Bob's question interleave in the same history. An opt-in mode keys context by
`{chat_id}:{user_id}` instead of just `{chat_id}`, giving each user a private
conversation with the agent inside the same group.

### Changes — `helpers/telegram_bridge.py`

New helper (alongside `_topic_key`):
```python
def _conversation_key(
    chat_id: str,
    thread_id: Optional[int],
    user_id: Optional[str],
    per_user: bool,
) -> str:
    """Build the context-lookup key based on isolation mode."""
    base = _topic_key(chat_id, thread_id)
    if per_user and user_id and thread_id is None:
        # Per-user only in group chats without topic context
        return f"{base}:user:{user_id}"
    return base
```

In `_on_message`, read config:
```python
per_user = config.get("chat_bridge", {}).get("per_user_context", False)
chat_type = message.chat.type  # "private", "group", "supergroup", "channel"
is_group = chat_type in ("group", "supergroup")
conv_key = _conversation_key(chat_id, thread_id, user_id, per_user and is_group)
```

Use `conv_key` everywhere `chat_id` was used for context lookup.

### `default_config.yaml` addition
```yaml
chat_bridge:
  per_user_context: false       # separate A0 context per user in group chats
```

---

## Track 17 — Auto-Summary on Context Expiry

### Goal
When a topic context expires (`topic_idle_timeout` fires), auto-run summarization on
the conversation history and save the result to A0's memory. This preserves project
knowledge even after the context is discarded, so the agent can refer to past topic
discussions without re-reading the full message history.

### Changes — `helpers/telegram_bridge.py`

Add `_maybe_expire_topic_context` called from `_on_forum_topic_created` and
`_on_message` when a topic key is resolved:

```python
async def _maybe_expire_and_summarize(self, topic_key: str, chat_id: str, thread_id: int):
    """If the topic context is idle-expired, summarize before discarding it."""
    config = self._get_config()
    timeout = config.get("chat_bridge", {}).get("supergroups", {}).get("topic_idle_timeout", 86400)
    if timeout == 0:
        return  # Never expire

    context_id = get_context_id(topic_key)
    if not context_id:
        return

    # Check last-activity timestamp stored in topic map
    topic_info = get_topic_project(topic_key)
    last_active = topic_info.get("last_active_at", 0) if topic_info else 0
    if time.time() - last_active < timeout:
        return  # Still within idle window

    # Idle timeout exceeded — summarize and clear
    logger.info(f"Topic {topic_key} idle timeout reached, summarizing...")
    try:
        from usr.plugins.telegram.helpers.message_store import get_messages
        messages = get_messages(chat_id, thread_id=thread_id, limit=200)
        if messages:
            from tools.telegram_summarize import _save_to_memory, SUMMARIZE_PROMPT
            from usr.plugins.telegram.helpers.telegram_client import format_messages
            formatted = format_messages(messages)
            # Use the bridge's agent context for summarization
            from agent import AgentContext
            ctx = AgentContext.get(context_id)
            if ctx:
                summary = await ctx.agent0.call_utility_model(
                    system="Summarize this Telegram topic conversation concisely.",
                    message=SUMMARIZE_PROMPT.format(messages=formatted),
                )
                await _save_to_memory(ctx.agent0, f"Topic: {topic_key}\n\n{summary}")
    except Exception as e:
        logger.warning(f"Auto-summary on expiry failed: {e}")

    # Clear the expired context
    set_context_id(topic_key, "")
```

Update `set_topic_project` to track `last_active_at` on every message.

---

## Updated Config Summary

Complete `default_config.yaml` with all new sections:

```yaml
chat_bridge:
  auto_start: false
  allowed_users: []
  allow_elevated: false
  auth_key: ""
  session_timeout: 300
  mode: "polling"               # polling | webhook

  # Track 1
  reactions:
    enabled: true
    on_received: "👍"
    on_thinking: "🤔"
    on_done: "✅"
    on_error: "❌"

  # Track 2
  streaming:
    enabled: false
    mode: "sentence"            # word | sentence | paragraph | none
    edit_interval_ms: 1500
    placeholder: "…"

  # Track 3
  supergroups:
    auto_create_topic_context: true
    auto_create_project: false
    topic_idle_timeout: 86400   # 0 = never expire

  # Track 4
  approvals:
    enabled: false
    timeout: 120
    require_for: []

  # Track 5
  handle_edited_messages: true
  edited_message_window: 600

  # Track 7
  webhook_url: ""
  webhook_secret: ""
  webhook_max_connections: 40

  # Track 8
  voice:
    enabled: true
    transcribe_audio: true
    max_duration_seconds: 300

  # Track 9
  attachments:
    max_size_mb: 20
    allowed_extensions: []
    download_in_restricted: false

  # Track 11
  watchdog:
    enabled: true
    check_interval: 60
    max_restarts_per_hour: 10

  # Track 12
  max_concurrent: 3

  # Track 16
  per_user_context: false
```

---

## Updated Files Matrix (all tracks)

| File | Tracks |
|------|--------|
| `helpers/telegram_bridge.py` | 1,2,3,4,5,6,8,9,10,11,12,15,16,17 |
| `helpers/telegram_client.py` | 2,3,4,6,7,8,9,13,14,15 |
| `helpers/stream_response.py` | 2 (new) |
| `helpers/rate_limiter.py` | 1,2 (new) |
| `helpers/button_builder.py` | 4 (new) |
| `helpers/conversation_store.py` | 10 (new) |
| `helpers/message_store.py` | 2,3,9 |
| `helpers/format_telegram.py` | 2 |
| `helpers/sanitize.py` | 3 |
| `helpers/poll_state.py` | 3 |
| `tools/telegram_manage.py` | 3 |
| `tools/telegram_send.py` | 2,3,4,13,15 |
| `tools/telegram_read.py` | 3 |
| `tools/telegram_chat.py` | 3 |
| `tools/telegram_summarize.py` | 3 |
| `prompts/agent.system.tool.telegram_manage.md` | 3 |
| `prompts/agent.system.tool.telegram_send.md` | 2,3,4,13,15 |
| `prompts/agent.system.tool.telegram_read.md` | 3 |
| `prompts/agent.system.tool.telegram_chat.md` | 3 |
| `prompts/agent.system.tool.telegram_summarize.md` | 3 |
| `skills/telegram-research/SKILL.md` | 3 |
| `skills/telegram-communicate/SKILL.md` | 3 |
| `skills/telegram-chat/SKILL.md` | 3 |
| `api/telegram_bridge_api.py` | 3 |
| `api/telegram_test.py` | 14 |
| `api/telegram_webhook_api.py` | 7 (new) |
| `extensions/python/agent_init/_10_telegram_chat.py` | 11 |
| `default_config.yaml` | all |
| `webui/config.html` | 1,2,3,4,7 |
| `webui/main.html` | 3,4,7,11,14 |
| `plugin.yaml` | version → 1.2.0 |
| `RELEASE.md` | all |
| `tests/regression_test.sh` | all |
| `tests/HUMAN_TEST_PLAN.md` | all |

---

## Full Implementation Order

Build in this sequence to minimise merge conflicts and dependency breaks:

1. `helpers/rate_limiter.py` — shared infra, no dependencies
2. `helpers/button_builder.py` — no dependencies
3. `helpers/conversation_store.py` — no dependencies
4. **Track 1** — emoji reactions (uses rate_limiter)
5. **Track 10** — conversation persistence (uses conversation_store, no other deps)
6. **Track 5** — edited message handling (small, isolated)
7. **Track 6** — bot commands (`/auth`, `/help`, `/newcontext`, uses conversation_store for reset)
8. **Track 12** — concurrency semaphore (wrap Track 1 reactions + agent dispatch)
9. **Track 2** — streaming (uses rate_limiter, format_telegram additions)
10. **Track 3a + 3b** — topic keying + thread-aware sending (sanitize.py, message_store.py first)
11. **Track 3c** — topic-to-project mapping (tool updates, dashboard panel)
12. **Track 3d** — forum management API
13. **Track 4** — inline buttons + approval workflow
14. **Track 8** — voice transcription
15. **Track 9** — document handling
16. **Track 11** — watchdog auto-restart
17. **Track 13** — polls
18. **Track 14** — privacy mode detection
19. **Track 15** — sticker + delete
20. **Track 16** — per-user context
21. **Track 17** — auto-summary on expiry
22. **Track 7** — webhook mode (last; production deployment concern, no feature deps)
