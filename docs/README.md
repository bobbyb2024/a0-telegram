# Telegram Integration Plugin Documentation

## Overview

Send, receive, and manage messages via Telegram Bot API with a full-featured real-time chat bridge for Agent Zero.

## Contents

- [Quick Start](QUICKSTART.md) — Installation and first-use guide
- [Setup](SETUP.md) — Detailed setup, credentials, and troubleshooting
- [Development](DEVELOPMENT.md) — Contributing and development setup

## Architecture

```
a0-telegram/
├── plugin.yaml              # Plugin manifest (name, version, settings_sections)
├── default_config.yaml      # Default settings for all config keys
├── initialize.py            # Dependency installer (aiohttp, pyyaml, python-telegram-bot)
├── install.sh               # Deployment helper script
├── hooks.py                 # Plugin lifecycle hooks (install/uninstall/save_plugin_config)
├── helpers/
│   ├── telegram_client.py   # Async REST client — all Telegram Bot API methods + rate limiting
│   ├── telegram_bridge.py   # Chat bridge bot (python-telegram-bot, polling + webhook)
│   ├── rate_limiter.py      # Token-bucket rate limiter (global/per-chat/edit/react buckets)
│   ├── sanitize.py          # Prompt injection defense, input validation, atomic file writes
│   ├── message_store.py     # Thread-safe persistent message storage
│   ├── conversation_store.py# Thread-safe persistent conversation history
│   ├── poll_state.py        # Background polling state
│   ├── button_builder.py    # Inline keyboard construction helpers
│   ├── stream_response.py   # Streaming edit loop with debouncing
│   └── format_telegram.py   # Markdown → Telegram HTML; streaming chunk formatting
├── tools/                   # 6 agent tools
│   ├── telegram_read.py     # Read messages, list chats, forum topics
│   ├── telegram_send.py     # Send text/photo/reaction/buttons/poll/sticker/edit/delete
│   ├── telegram_members.py  # List group administrators
│   ├── telegram_summarize.py# LLM-powered conversation summaries
│   ├── telegram_manage.py   # Chat management, forum topic CRUD + mapping
│   └── telegram_chat.py     # Chat bridge lifecycle control
├── prompts/                 # Tool prompt files (one per tool)
├── skills/                  # 3 skills (research, communicate, chat)
├── api/
│   ├── telegram_test.py     # POST /api/plugins/telegram/telegram_test
│   ├── telegram_config_api.py # POST /api/plugins/telegram/telegram_config_api
│   ├── telegram_bridge_api.py # POST /api/plugins/telegram/telegram_bridge_api
│   └── telegram_webhook_api.py # POST /api/plugins/telegram/webhook
├── extensions/
│   └── python/agent_init/
│       └── _10_telegram_chat.py  # Auto-start bridge + watchdog on agent init
├── webui/
│   ├── config.html          # Settings UI (Alpine createStore gate pattern)
│   └── main.html            # Dashboard UI (Alpine createStore + toast notifications)
├── tests/                   # Regression suite + human verification plan
└── docs/                    # This documentation
```

## Data Flow

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Agent Zero  │────>│  telegram_client  │────>│  Telegram Bot   │
│  (tools)     │<────│  (async REST)     │<────│  API Server     │
└─────────────┘     └──────────────────┘     └─────────────────┘
                       │  Rate limiter:
                       │  30/s global · 1/s per-chat
                       │  20/min per-msg edit · 10/s react
                       │  429 RetryAfter handling

Direct Tools:
┌──────────┐     ┌─────────────────────────────────┐
│  Agent   │────>│  Tools (6): read / send /        │
│  Zero    │<────│  members / summarize / manage /  │
└──────────┘     │  chat                            │
                 └─────────────────────────────────┘

Chat Bridge (polling or webhook):
┌──────────┐     ┌────────────────────────────────────────────┐
│ Telegram │────>│ telegram_bridge (python-telegram-bot)       │
│ Users    │<────│                                             │
└──────────┘     │  Sanitize → route → respond                │
                 │                                             │
                 │  Restricted: call_utility_model (no tools) │
                 │  Elevated:   context.communicate (full)    │
                 │                                             │
                 │  Streaming edits · Reaction lifecycle      │
                 │  Voice transcription · Approval buttons    │
                 │  Forum topic routing · Per-user context    │
                 └────────────────────────────────────────────┘
                           ↑ Watchdog monitors + restarts
```

## Security Layers

1. **Prompt injection defense** — NFKC normalization, invisible char stripping, 33-phrase injection regex, delimiter escaping; applied to all inbound text including voice transcripts
2. **CSRF protection** — All API endpoints require CSRF tokens (webhook endpoint exempt; uses HMAC secret token instead)
3. **Token masking** — Bot tokens masked in API responses
4. **User allowlist** — Bridge only responds to authorized user IDs
5. **Two-mode architecture** — Restricted (utility model, no tools) vs. Elevated (full agent loop, requires `!auth`)
6. **Auth key** — HMAC constant-time comparison (`hmac.compare_digest`); brute-force lockout after 5 failures
7. **Session timeout** — Elevated sessions auto-expire (default 5 min); per-user, per-chat
8. **Approval workflow** — Requester user ID stored and verified; cross-user bypass prevented
9. **Rate limiting** — Token-bucket enforced on every API call (global + per-chat + per-edit + per-react); 429 RetryAfter respected
10. **Webhook HMAC** — `X-Telegram-Bot-Api-Secret-Token` validated via `hmac.compare_digest`
11. **Atomic file writes** — All state files written via `os.open(O_CREAT|O_TRUNC, 0o600)` + `os.replace()`; no partial writes or world-readable files
12. **Threading safety** — `threading.Lock` on message store, conversation store, and all chat-state load-mutate-save cycles

## Tools (6)

| Tool | Actions | Description |
|------|---------|-------------|
| `telegram_read` | `messages`, `chats`, `chat_info`, `topics` | Read messages and chat info; thread-aware topic filtering |
| `telegram_send` | `send`, `reply`, `forward`, `react`, `photo`, `edit`, `delete`, `send_buttons`, `poll`, `stop_poll`, `sticker` | Send all content types; buttons, polls, reactions |
| `telegram_members` | `list`, `search` | List and search group administrators |
| `telegram_summarize` | — | LLM-powered summary; auto-save to agent memory; topic-key aware |
| `telegram_manage` | `pin`, `unpin`, `set_title`, `set_description`, `create_topic`, `rename_topic`, `close_topic`, `reopen_topic`, `map_topic`, `unmap_topic`, `list_topics` | Full chat and forum topic management |
| `telegram_chat` | `add`, `remove`, `list`, `start`, `stop`, `restart`, `status` | Bridge control and chat list management |

## Skills (3)

| Skill | Category | Trigger Phrases |
|-------|----------|-----------------|
| `telegram-research` | Read & analyze | "read telegram messages", "summarize chat", "get telegram history" |
| `telegram-communicate` | Send & manage | "send telegram message", "manage chat", "pin message" |
| `telegram-chat` | Bridge operation | "start telegram bridge", "chat via telegram", "connect telegram" |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/plugins/telegram/telegram_test` | POST | Test bot connection; returns username, privacy mode, advisory |
| `/api/plugins/telegram/telegram_config_api` | POST | `generate_auth_key` action |
| `/api/plugins/telegram/telegram_bridge_api` | POST | `start`, `stop`, `restart`, `status`, `list_topics`, `map_topic`, `unmap_topic` |
| `/api/plugins/telegram/webhook` | POST | Telegram webhook receiver; CSRF-exempt; validates secret token |

> Config load/save is handled by A0's plugin settings framework. Endpoint handlers only cover logic requiring server-side execution.

## Key Helpers

| Module | Purpose |
|--------|---------|
| `telegram_client.py` | All Telegram Bot API methods; `_request()` applies rate-limiter buckets before every call; `_do_request()` handles 429 RetryAfter |
| `telegram_bridge.py` | PTB Application; message handlers; streaming; reactions; voice; approvals; forum topics; watchdog state tracking |
| `rate_limiter.py` | Async token-bucket; `acquire(key)` keys: `global`, `chat:{id}`, `edit:{id}:{msg_id}`, `react:{id}` |
| `sanitize.py` | `sanitize_content()`, `sanitize_filename()`, `validate_chat_id()`, `validate_image_url()`, `secure_write_json()` |
| `conversation_store.py` | Load/save/append/clear conversation history; `threading.Lock` on all I/O |
| `message_store.py` | Store/retrieve messages by chat key or topic key; `threading.Lock` on all I/O |
| `stream_response.py` | `stream_text_to_telegram()` — sentence splitter, edit loop, `▌` cursor, final HTML render |
| `button_builder.py` | `approval_buttons()`, `choice_buttons()` (index-based callback_data), `yes_no_buttons()`, `url_button()` |

## Verification

- **58/58 regression tests** passed (v1.1.0 baseline)
- **76/76 human verification tests** passed (v1.1.0 baseline)
- **Security assessment** completed — 31 findings addressed across v1.1.x and v1.2.0
- See [../tests/](../tests/) for test plans and results

## API Pricing

**Free** — The Telegram Bot API is free for all bots. No paid tiers, no rate limit subscriptions.
