# Telegram Integration Plugin for Agent Zero

Send, receive, and manage messages via Telegram Bot API with a full-featured real-time chat bridge.

## Features

### Core Tools
- **Read messages** from chats, groups, forum topics, and channels with thread-aware filtering
- **Send messages** — text, photos, reactions, replies, forwards, polls, stickers, and inline-button prompts
- **Streaming responses** — bot edits its reply progressively as text is generated (word / sentence / paragraph mode)
- **Emoji reaction lifecycle** — bot reacts 👍 (received) → 🤔 (thinking) → ✅/❌ (done/error); all emojis configurable
- **List members** (administrators) of groups and supergroups
- **Summarize conversations** with LLM and auto-save to agent memory
- **Manage chats** — pin/unpin messages, set title/description, create/rename/close/reopen forum topics, map topics to Agent Zero projects
- **Chat bridge** — real-time Telegram-to-Agent Zero LLM conversation

### Chat Bridge
- **Restricted mode** (default) — pure LLM chat, no tools, no code execution
- **Elevated mode** — authenticated users get full Agent Zero agent loop from Telegram
- **Voice transcription** — voice messages and audio files transcribed via utility model before routing
- **Document & photo handling** — files in elevated mode attached to agent context
- **Edited message handling** — bot deletes prior reply and regenerates when user edits their message
- **Per-user context isolation** — each Telegram user can have their own conversation history within a shared chat
- **Conversation persistence** — history survives bridge and agent restarts
- **Concurrency control** — configurable limit on simultaneous agent responses (default 3)
- **Inline approval workflow** — agent can present Yes/No button prompts; response suspends until user clicks or timeout
- **Slash command menu** — `/auth`, `/deauth`, `/status`, `/help`, `/newcontext`, `/cancel` registered in Telegram's command menu
- **Forum topic routing** — each supergroup forum topic routes to an isolated Agent Zero project context
- **Bridge watchdog** — auto-restarts the bridge on crash/stall with exponential backoff
- **Privacy mode detection** — dashboard warns when bot privacy mode is ON and the bot cannot read group messages

### Delivery Modes
- **Long-polling** (default) — no infrastructure required
- **Webhook mode** — Telegram POSTs updates directly to your public HTTPS endpoint; validated via `X-Telegram-Bot-Api-Secret-Token`

## Quick Start

1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram
2. Install the plugin via Plugin Hub, or:
   ```bash
   ./install.sh
   ```
3. Configure the bot token in WebUI → Settings → External Services → Telegram Integration
4. Restart Agent Zero
5. Ask the agent: *"List my Telegram chats"*

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the full step-by-step guide.

## Tools

| Tool | Actions |
|------|---------|
| `telegram_read` | `messages` — read recent messages<br>`chats` — list monitored chats<br>`chat_info` — get chat metadata<br>`topics` — list forum topic mappings |
| `telegram_send` | `send` — send text (auto-splits >4096 chars)<br>`reply` — reply to a message<br>`forward` — forward a message<br>`react` — set emoji reaction<br>`photo` — send a photo with optional caption<br>`edit` — edit a sent message<br>`delete` — delete a message<br>`send_buttons` — send message with inline keyboard<br>`poll` — create a poll<br>`stop_poll` — close a poll<br>`sticker` — send a sticker |
| `telegram_members` | `list` — list group admins<br>`search` — search by name |
| `telegram_summarize` | Summarize recent messages with LLM; auto-save to agent memory |
| `telegram_manage` | `pin` / `unpin` — pin messages<br>`set_title` / `set_description` — update chat metadata<br>`create_topic` / `rename_topic` / `close_topic` / `reopen_topic` — forum topic management<br>`map_topic` / `unmap_topic` / `list_topics` — project mapping |
| `telegram_chat` | `add` / `remove` / `list` — manage bridge chat list<br>`start` / `stop` / `restart` / `status` — bridge lifecycle |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/plugins/telegram/telegram_test` | POST | Test connection; returns bot info, privacy mode, and advisory |
| `/api/plugins/telegram/telegram_config_api` | POST | Generate auth key |
| `/api/plugins/telegram/telegram_bridge_api` | POST | Bridge start/stop/restart/status; topic list/map/unmap |
| `/api/plugins/telegram/webhook` | POST | Webhook receiver (active when `webhook.enabled: true`) |

## Chat Bridge — Security

### Two-Mode Architecture

**Restricted Mode** (default for all users)
- Messages route through `call_utility_model()` — a direct LLM call with **no tools, no code execution, no file access**.
- The agent literally cannot perform system operations regardless of what the message says.

**Elevated Mode** (requires config + authentication)
- Messages route through the full Agent Zero agent loop via `context.communicate()`.
- Requires all three: `allow_elevated: true` in config, user on the allowlist, and runtime `!auth` per session.

### Authentication

| Command | Description |
|---------|-------------|
| `!auth <key>` | Authenticate for elevated mode. Message deleted immediately after to protect the key. |
| `!deauth` | End elevated session; conversation history cleared. Aliases: `!dauth`, `!unauth`, `!logout`, `!logoff` |
| `!status` | Show current mode and session expiry. Alias: `!bridge-status` |

Or use the Telegram slash command menu: `/auth`, `/deauth`, `/status`, `/help`, `/newcontext`, `/cancel`

- Auth key auto-generated when elevated mode is enabled; shown in WebUI settings
- **Constant-time comparison** (`hmac.compare_digest`) prevents timing attacks
- **Brute-force protection** — 5 failed attempts in 5 minutes locks out the user

### Session Management

- Elevated sessions expire after a configurable timeout (default: **5 minutes**)
- Sessions are **per-user, per-chat** — elevation in one chat does not carry over
- Sessions are **in-memory only** — bridge or agent restart ends all sessions

### User Allowlist

`allowed_users` in config restricts who can interact with the bridge at all. Non-listed users are silently ignored. Empty list allows everyone.

### Input Sanitization

All inbound text (including voice transcripts) passes through `sanitize_content()`:
- NFKC Unicode normalization
- Zero-width / invisible character stripping (25 code points)
- Prompt-injection pattern matching (33 phrases: system prompt overrides, role-play attempts, delimiter injections)
- Delimiter tag escaping (`<|`, `[INST]`, etc.)
- Message length cap (4096 characters)

Usernames sanitized separately to prevent display-name injection.

### Rate Limiting

- Token-bucket rate limiter enforced on every Telegram API call:
  - **Global**: 30 requests/second
  - **Per-chat send**: 1 message/second
  - **Per-message edit**: 20 edits/minute
  - **Per-chat reaction**: 10 reactions/second
- **429 RetryAfter** handling — respects Telegram's `retry_after` parameter; retries automatically
- **Message rate limit**: 10 messages per 60-second window per user (bridge)
- **Auth failure rate limit**: 5 attempts per 5-minute window per user

### Webhook Security

When `webhook.enabled: true`, the endpoint validates `X-Telegram-Bot-Api-Secret-Token` via `hmac.compare_digest`. Without a secret token, a warning is shown in the WebUI settings panel and logged at WARNING level.

### Recommendations

1. **Always configure a User Allowlist** — restrict who can reach the bot
2. **Keep session timeouts short** — 5 minutes is a reasonable default
3. **Use private chats or private groups** — avoid public groups
4. **Set a webhook secret token** if using webhook mode
5. **Rotate auth keys regularly** — regenerate in WebUI settings

## Configuration

All settings are managed in WebUI → Settings → External Services → Telegram Integration.

| Section | Key Settings |
|---------|-------------|
| **Bot Account** | Bot token |
| **Access Control** | Allowed chat IDs |
| **Chat Bridge** | Auto-start, max concurrent responses, per-user context, user allowlist, elevated mode + auth key + session timeout |
| **Emoji Reactions** | Enable/disable; received / processing / success / error emojis |
| **Streaming Responses** | Enable/disable; granularity (word/sentence/paragraph); edit interval (ms) |
| **Supergroups & Forum Topics** | Auto-context on new topic; topic project ID prefix |
| **Approval Workflow** | Button timeout (seconds) |
| **Webhook Mode** | Enable/disable; public URL; secret token |
| **Bridge Watchdog** | Enable/disable; stale timeout (seconds); max auto-restarts |

Alternatively set `TELEGRAM_BOT_TOKEN` as an environment variable.

## What's New in v1.2.0

### New Features
| Feature | Description |
|---------|-------------|
| Streaming responses | Progressive message edits — word / sentence / paragraph modes with configurable edit interval |
| Emoji reaction lifecycle | 👍→🤔→✅/❌ per-message status signals; all emojis configurable |
| Forum topic routing | Each topic → isolated Agent Zero project context; auto-created on new topic |
| Voice transcription | Voice messages transcribed via utility model before LLM routing |
| Document & photo handling | Files attached to agent context in elevated mode |
| Edited message handling | Prior reply deleted; response regenerated on message edit |
| Inline approval workflow | Agent presents Yes/No inline buttons; request suspends until user responds |
| Slash command menu | `/auth` `/deauth` `/status` `/help` `/newcontext` `/cancel` in Telegram menu |
| Webhook mode | Full webhook receiver with HMAC secret-token validation |
| Bridge watchdog | Auto-restart on crash/stall; exponential backoff 10s→300s; resets after 1h clean run |
| Conversation persistence | History survives bridge and agent restarts |
| Per-user context isolation | Optional per-user conversation scoping within shared chats |
| Concurrency control | `asyncio.Semaphore` limits simultaneous agent responses |
| Polls | `poll` and `stop_poll` send actions |
| Sticker & delete actions | `sticker` and `delete` send actions |
| Privacy mode detection | Dashboard advisory when bot privacy mode is ON |
| Per-message edit rate limit | Token-bucket capped at 20 edits/minute per message |
| Forum topic dashboard | WebUI panel shows topic-to-project mappings with Load button |
| Stats grid | WebUI dashboard shows Bridge Chats, Topics, Restarts, Update mode |

### Bug Fixes (v1.1.x → v1.2.0)
| Bug | Fix |
|-----|-----|
| `!status` command not recognized | Added to auth command dispatch |
| Elevated mode "infection" check false positive | Rewritten mode detection logic |
| Auto-start extension non-functional | Complete rewrite of agent init extension |
| getUpdates conflict during simultaneous polling | Guard function added; polling and bridge now mutually exclusive |
| Race condition on chat state file under concurrent tool calls | Module-level `threading.Lock` wraps all load-mutate-save cycles |
| Approval bypass — any allowlisted user could resolve any pending approval | `requester_user_id` stored and verified before resolving Future |
| Partial writes to state/store files on crash | Atomic writes via `os.open(O_CREAT|O_TRUNC, 0o600)` + `os.replace()` |
| World-readable message store | Secure write enforces `0o600` on all state files |
| Rate limiter defined but never called | Wired into `TelegramClient._request()` with per-method bucket classification |
| 429 responses retried without back-off | `_do_request()` reads `retry_after`, sleeps, retries once |
| Prompt injection via voice transcript | Voice transcript sanitized immediately after transcription |
| Thread-unsafe message and conversation stores | `threading.Lock` added to both stores |
| `callback_data` >64 bytes crashing `send_buttons` | `_normalise_buttons()` now caps at Telegram's 64-byte limit |
| Null bytes in sanitized filenames | `sanitize_filename()` strips `\x00` before processing |

## New Files in v1.2.0

| File | Purpose |
|------|---------|
| `helpers/rate_limiter.py` | Token-bucket rate limiter: global / per-chat / per-message-edit / per-chat-react |
| `helpers/button_builder.py` | Inline keyboard helpers (`approval_buttons`, `choice_buttons`, `yes_no_buttons`, `url_button`) |
| `helpers/conversation_store.py` | Thread-safe persistent conversation history |
| `helpers/stream_response.py` | Streaming edit loop with sentence splitter and debouncing |
| `api/telegram_webhook_api.py` | Webhook POST receiver with HMAC secret-token validation |

## Testing

```bash
bash tests/regression_test.sh [container_name] [port]
```

58+ automated tests covering container health, installation, imports, API endpoints,
sanitization, tool imports, prompts, skills, WebUI, framework compatibility, and security.

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Setup Guide](docs/SETUP.md)
- [Development Guide](docs/DEVELOPMENT.md)
- [Full Documentation](docs/README.md)
- [Release Notes](RELEASE.md)

## License

MIT — see [LICENSE](LICENSE)
